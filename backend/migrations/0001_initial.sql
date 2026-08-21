-- EvidWell initial schema
--
-- Apply with:  python -m scripts.migrate
--          or  psql "$DATABASE_URL" -v embedding_dim=1024 -f migrations/0001_initial.sql
--
-- The embedding dimension is templated because changing it later is a table
-- migration plus a re-embed of every cached row, not a config change. It must
-- match settings.embedding_dim in the application config.
--
-- ---------------------------------------------------------------------------
-- This file is a squashed baseline, collapsed three times. The first pass
-- folded in retry bookkeeping, the removal of an unused HNSW index, and run
-- heartbeats. The second (2026-08-21) folded in the `narrative_review` study
-- type and the per-stage token ledger. The third, the same day, folded in
-- retraction tracking. Each change's reasoning is preserved at the point it
-- applies — beside the column, the enum value, or the absent index it explains
-- — rather than as history at the top of the file.
--
-- **The condition, not the habit.** Squashing is free only while every database
-- holding this schema can be dropped and rebuilt. Nothing is deployed, so that
-- is still true; it stops being true the first time this runs somewhere whose
-- contents are not reproducible. The second squash was not free even here — the
-- local database held 92 cached sources and two articles, and rebuilding it
-- meant a pg_dump of the data, a drop of the volume, and a restore. That is
-- affordable for one developer and for nobody else.
--
-- scripts/migrate.py records a sha256 per filename and refuses to re-run a file
-- whose contents changed, which is what makes squashing a deliberate act rather
-- than an accident. From here every schema change is a new numbered file,
-- starting at 0002.
-- ---------------------------------------------------------------------------

\set ON_ERROR_STOP on
\if :{?embedding_dim} \else \set embedding_dim 1024 \endif

BEGIN;

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()

-- ---------------------------------------------------------------------------
-- Enumerated domains
-- ---------------------------------------------------------------------------

-- validation_failed and draft_failed are terminal PRE-QUEUE states. They exist
-- so a failure is visible rather than silent; they are never reachable from
-- pending_review.
CREATE TYPE article_status AS ENUM (
    'pending_review',
    'published',
    'rejected',
    'validation_failed',
    'draft_failed'
);

CREATE TYPE verdict AS ENUM (
    'supported',
    'mixed',
    'weak',
    'no_evidence'
);

-- Ordered weakest -> strongest. Postgres orders enum values by declaration
-- order, so `study_type > 'observational'` works and grade comparisons in SQL
-- read naturally.
-- `narrative_review` sits below `observational` and above `case_report`, and
-- the position is the whole point of the value. It was added after the first
-- live runs, where 38 of 92 cached sources classified `unknown` and 16 of those
-- were plainly tagged "Review". UNKNOWN is the wrong home for them: it means
-- "no usable publication type" — uncertainty, which the hierarchy deliberately
-- treats as weakest. A narrative review is not uncertain; we read the tag, and
-- it is weak *secondary* evidence. Collapsing the two makes the unknown count
-- undiagnosable, and the two situations call for opposite responses (fix the
-- classifier vs. fix the query). Below `observational` because it contributes
-- no primary data and has no stated method protecting it from selection bias;
-- above `case_report` because it surveys a literature rather than one patient.
-- Verdict ceiling `weak`.
--
-- Declaration order here must match domain/enums.py, which EVIDENCE_RANK is
-- derived from. Nothing sorts on the type in SQL today; the enum's docstring
-- promises the two orders agree, and a promise that quietly stops being true is
-- worse than one never made.
--
-- Adding a tier is only half the change: classification is Python, so no
-- migration can reclassify rows already cached. Run
-- `python -m scripts.reclassify_sources` against an existing database.
CREATE TYPE study_type AS ENUM (
    'unknown',
    'in_vitro',
    'animal',
    'case_report',
    'narrative_review',
    'observational',
    'rct',
    'systematic_review',
    'meta_analysis'
);

CREATE TYPE user_role AS ENUM ('admin', 'reviewer');

CREATE TYPE run_status AS ENUM ('queued', 'running', 'succeeded', 'failed', 'cancelled');

-- ---------------------------------------------------------------------------
-- users — reviewers. Deliberately minimal, but reviewed_by is a real FK so
-- "who approved this" stays answerable.
-- ---------------------------------------------------------------------------

CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,          -- argon2id
    display_name  TEXT NOT NULL,
    role          user_role NOT NULL DEFAULT 'reviewer',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX users_email_key ON users (lower(email));

-- ---------------------------------------------------------------------------
-- sources — the growing library of cited literature. Also the vector store.
--
-- One row per paper, one embedding per abstract (no chunking in the MVP).
-- Metadata columns are stored but NOT embedded; they drive the evidence-grade
-- filter during re-ranking.
-- ---------------------------------------------------------------------------

CREATE TABLE sources (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pmid           TEXT,
    doi            TEXT,
    title          TEXT NOT NULL,
    abstract       TEXT NOT NULL,
    journal        TEXT,
    year           SMALLINT,
    study_type     study_type NOT NULL DEFAULT 'unknown',
    -- What the provider actually said, before normalisation. Kept so the
    -- classifier can be re-run over the cache without re-fetching.
    raw_study_type TEXT,
    citation_count INTEGER,
    url            TEXT,
    source_api     TEXT NOT NULL,          -- 'pubmed' | 'europe_pmc' | 's2' | 'openalex'
    embedding      vector(:embedding_dim),
    embedding_model TEXT,                  -- provider+model that produced the vector
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Retraction record. A citation is a promise that a study says what we
    -- claim it says; a retraction withdraws the study, and nothing about a
    -- cached row would otherwise notice. Written by
    -- scripts/check_retractions.py, which asks PubMed and Crossref. DESIGN.md §4.
    --
    -- Detection time, not the journal's retraction date: neither provider
    -- reliably exposes the latter, and inventing precision we do not have makes
    -- a worse record than an honest one.
    retracted_at   TIMESTAMPTZ,

    -- An expression of concern: the journal is investigating and has withdrawn
    -- nothing. Separate from retracted_at because the two call for opposite
    -- handling — a retraction refuses the paper, a concern records it and lets
    -- a human weigh it. Collapsing them would either suppress papers that go on
    -- to be exonerated, or admit ones that have been withdrawn.
    concern_at     TIMESTAMPTZ,

    -- **The column that makes the other two trustworthy.** NULL means nobody
    -- has successfully asked — not "asked and it is fine" — and that difference
    -- is the whole failure mode here. Written only when a provider actually
    -- answered: never on a transport error, a 500, or an id it cannot resolve.
    -- Without it, a sweep that reached nothing has the same shape as one that
    -- verified everything, and the next sweep trusts the first.
    --
    -- Same rule as retrieval/throttle.py one layer down: a search that could not
    -- run must never look like a search that found nothing. NCBI makes it
    -- concrete — it returns an `error` record rather than omitting an unknown
    -- id, and reading that as "no retraction tags" marked papers nobody could
    -- look up as verified clean.
    retraction_checked_at TIMESTAMPTZ,

    -- Which provider said what, e.g. "pubmed: retracted publication". Free text
    -- and never parsed: it exists so a reviewer looking at a flagged article can
    -- see the basis without re-querying two APIs by hand.
    retraction_note TEXT,

    -- Invariant #2 depends on this: a source with neither identifier can never
    -- satisfy "resolves to a real PMID/DOI", so it must not exist.
    CONSTRAINT sources_identifier_required CHECK (pmid IS NOT NULL OR doi IS NOT NULL)
);

-- The sweep's driving query: oldest check first. Partial on `retracted_at IS
-- NULL` because a retraction is not un-issued, so a paper already known
-- retracted needs no re-check — the index covers only the rows the sweep walks
-- and stays small as the flagged set grows.
CREATE INDEX sources_retraction_sweep_idx
    ON sources (retraction_checked_at NULLS FIRST)
    WHERE retracted_at IS NULL;

-- Partial unique indexes make the cache upsert a single ON CONFLICT rather
-- than a read-then-write race between concurrent retrieval fan-outs.
CREATE UNIQUE INDEX sources_doi_key  ON sources (lower(doi)) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX sources_pmid_key ON sources (pmid)       WHERE pmid IS NOT NULL;

CREATE INDEX sources_study_type_year_idx ON sources (study_type, year DESC);

-- ---------------------------------------------------------------------------
-- There is deliberately NO ANN index on sources.embedding. This is the second
-- attempt at that decision: an earlier version of this schema created an HNSW
-- index here and nothing could ever use it.
--
-- The one vector query in the codebase is SemanticReranker.rank_for_claim, and
-- it cannot use an ANN index. Its final score is cosine + grade bonus +
-- recency bonus, so top-k is applied in Python and no LIMIT reaches SQL;
-- combined with a restrictive `id = ANY(...)` filter over the run's ~100
-- candidates, Postgres plans a bitmap scan on sources_pkey and an exact
-- quicksort. EXPLAIN ANALYZE confirms an HNSW index is never chosen — and the
-- exact sort is the faster plan at that size (0.53ms against 1.44ms over 3000
-- rows), as well as being exact rather than approximate.
-- tests/test_rerank_plan.py pins this with EXPLAIN.
--
-- Meanwhile the index was the most expensive thing about writing to `sources`,
-- which is the table designed to grow forever and written on every run.
-- Measured on 1500 inserts of 1024-dimension vectors:
--
--     with the index     5.26 s     (index 27 MB)
--     without it         0.11 s
--
-- Roughly 48x on insert, for zero reads.
--
-- CREATE IT when a query actually needs approximate nearest-neighbour search —
-- meaning one that issues `ORDER BY embedding <=> $1 LIMIT k` with no
-- restrictive WHERE. The two candidates are the v2 full-text pass over
-- `source_passages` (stubbed at the end of this file) and a related-articles
-- feature. Neither exists, and adding the index before one of them does is
-- paying the 48x for a query nobody has written.
--
--     CREATE INDEX CONCURRENTLY sources_embedding_hnsw
--         ON sources USING hnsw (embedding vector_cosine_ops)
--         WITH (m = 16, ef_construction = 64);
--
-- CONCURRENTLY because by then the table will be large and the build slow. It
-- cannot run inside a transaction, so it does not belong in this file at all —
-- it needs its own migration or a maintenance script. Set hnsw.ef_search
-- per-session at query time; the index-time default of 40 is low for a top-k
-- of 8. Revisit rank_for_claim first: as written, it will not use the index.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- articles
--
-- original_content is the immutable AI draft. edited_content is the
-- human-approved version. The public feed renders COALESCE(edited, original).
-- Both are TipTap documents (see DESIGN.md §3.4).
-- ---------------------------------------------------------------------------

CREATE TABLE articles (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug              TEXT NOT NULL,
    status            article_status NOT NULL DEFAULT 'pending_review',

    -- Input that produced this article
    topic             TEXT NOT NULL,
    source_blurb      TEXT,
    product           TEXT NOT NULL,
    target_claims     TEXT[] NOT NULL DEFAULT '{}',
    ingredients       TEXT[] NOT NULL DEFAULT '{}',

    -- Model output
    headline          TEXT NOT NULL,
    summary           TEXT NOT NULL,
    verdict           verdict NOT NULL,
    verdict_qualifier TEXT,
    original_content  JSONB NOT NULL,
    edited_content    JSONB,

    -- Derived card fields. Materialised at publish time by services/card.py
    -- so the feed query stays a single index scan. Derived, never generated —
    -- card and article cannot contradict each other.
    card_headline     TEXT,
    card_excerpt      TEXT,
    card_verdict      verdict,

    -- Evidence posture
    evidence_grade    study_type NOT NULL DEFAULT 'unknown',  -- best grade among cited
    validation_report JSONB,       -- {passed, citations_total, citations_resolved, failures[]}

    -- Review trail
    reviewed_by       UUID REFERENCES users (id) ON DELETE SET NULL,
    reviewed_at       TIMESTAMPTZ,
    rejection_reason  TEXT,
    published_at      TIMESTAMPTZ,

    -- Raised when a cited source is found retracted. **The article stays
    -- `published`.** That is not timidity about the schema — it is invariant #1
    -- read in the direction it actually points. ReviewService.approve() is the
    -- only thing that may set `published`, because a *human* decides what the
    -- public sees; an automatic withdrawal is the same machine making the same
    -- call in the other direction, on content already live. One retracted
    -- source among several does not necessarily invalidate a conclusion.
    --
    -- So this flag does three things and no more: it raises a banner on the
    -- public article, it puts the row in front of a reviewer, and it records
    -- when. A human then edits, or withdraws by hand. Anything stronger is a
    -- conversation about invariant #1, not a column.
    retraction_flagged_at TIMESTAMPTZ,

    -- What was found, for the reviewer who has to act on it: the source ids
    -- involved, so the console can point at the paragraph rather than saying
    -- "something in here is wrong".
    retraction_detail JSONB,

    pipeline_run_id   UUID,        -- FK added after pipeline_runs is created
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Invariant #1, at the storage layer: publication requires an identified
    -- human and a timestamp. No application bug can produce a published
    -- article with no reviewer.
    CONSTRAINT articles_published_requires_reviewer CHECK (
        status <> 'published'
        OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL AND published_at IS NOT NULL)
    ),
    CONSTRAINT articles_rejected_requires_reason CHECK (
        status <> 'rejected' OR rejection_reason IS NOT NULL
    )
);

CREATE UNIQUE INDEX articles_slug_key ON articles (slug);

-- The only index the public feed needs.
CREATE INDEX articles_feed_idx ON articles (published_at DESC) WHERE status = 'published';

-- The only index the review queue needs.
CREATE INDEX articles_queue_idx ON articles (created_at DESC) WHERE status = 'pending_review';

-- Flagged articles are a handful out of the whole table and are queried by
-- their presence, so the index carries only them.
CREATE INDEX articles_retraction_flagged_idx
    ON articles (retraction_flagged_at DESC)
    WHERE retraction_flagged_at IS NOT NULL;

-- ---------------------------------------------------------------------------
-- article_sources — provenance. Which paper backed which claim, under which
-- citation handle. This is what makes the review UI's sources panel one query.
-- ---------------------------------------------------------------------------

CREATE TABLE article_sources (
    article_id      UUID NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
    source_id       UUID NOT NULL REFERENCES sources (id) ON DELETE RESTRICT,
    claim           TEXT NOT NULL,
    -- The S-handle this source was given in the synthesis prompt ('S1', 'S2'…).
    -- Stored because validation and the editor both address sources by handle.
    citation_handle TEXT NOT NULL,
    -- Whether the model actually cited it, vs. it merely being in the prompt.
    -- Uncited sources are kept so a reviewer can see what was retrieved and
    -- left out — omission is the failure mode human review exists to catch.
    was_cited       BOOLEAN NOT NULL DEFAULT FALSE,
    relevance_score REAL,

    PRIMARY KEY (article_id, source_id, claim)
);

CREATE UNIQUE INDEX article_sources_handle_key
    ON article_sources (article_id, citation_handle, claim);

CREATE INDEX article_sources_source_idx ON article_sources (source_id);

-- ---------------------------------------------------------------------------
-- pipeline_runs / pipeline_stage_runs — observability, and the state a retry
-- is driven from.
--
-- Without these, a draft that fails citation validation looks identical to a
-- pipeline that never ran. Each stage row maps 1:1 onto a future Step
-- Functions state.
--
-- The retry columns exist because the orchestrator commits after each stage
-- rather than wrapping a whole multi-minute run in one transaction. A run that
-- fails at synthesis leaves its `sources` rows and their embeddings behind —
-- the expensive half of a run — and that work is only actually saved if
-- something retries.
-- ---------------------------------------------------------------------------

CREATE TABLE pipeline_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           TEXT NOT NULL,
    source_blurb    TEXT,
    status          run_status NOT NULL DEFAULT 'queued',
    article_id      UUID REFERENCES articles (id) ON DELETE SET NULL,
    error           JSONB,

    -- Lifetime token rollup over the per-stage ledger below, accumulated across
    -- attempts rather than assigned — a run that burned a synthesis budget,
    -- requeued and then succeeded spent both, and assigning would report the
    -- cheaper attempt as the whole cost. Four counters, not two, because the
    -- classes are priced an order of magnitude apart in both directions: a
    -- cache read is a tenth of a fresh input token, a cache write a quarter
    -- more. Folding them together makes caching look free *and* the first call
    -- look cheap, so the errors do not even cancel.
    --
    -- Not priceable as a unit: extraction and synthesis are separately
    -- configurable and may be different models at different rates. The model id
    -- lives on the stage row, and cost is computed at read time — see
    -- app/llm/pricing.py and DESIGN.md §3.7.
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,

    requested_by    UUID REFERENCES users (id) ON DELETE SET NULL,

    -- Attempts *started*, incremented when a worker claims the run rather than
    -- when one finishes. 0 until then, so "queued but never picked up" and
    -- "attempted once" stay distinguishable — and a run that kills the worker
    -- claiming it still spends budget, so stale recovery cannot resurrect it
    -- indefinitely.
    attempts        SMALLINT NOT NULL DEFAULT 0,

    -- Earliest time this run may be claimed. NULL means now. A retryable
    -- failure is usually a provider asking us to slow down, and a run requeued
    -- without a delay is re-claimed on the next 5-second poll — which is not a
    -- retry, it is a second failure.
    next_attempt_at TIMESTAMPTZ,

    -- Proof of life for an in-flight run. The worker pings this on a timer
    -- while a run executes, and a sweep in the poll loop recovers rows that
    -- have gone quiet for WORKER_STALE_AFTER_SECONDS.
    --
    -- Staleness is deliberately NOT measured from started_at. A duration
    -- cutoff is wrong in both directions at once:
    --
    --   * too slow — a worker killed at 14:00 is not recoverable until 14:30,
    --     and if the worker restarts before then the boot-time sweep skips the
    --     row and never looks again, so the run is stuck in `running` forever;
    --   * too aggressive — a legitimately long run (a cold local model, a
    --     provider making us wait out its backoff) crosses the cutoff while it
    --     is *still executing*, gets requeued, and runs a second time
    --     concurrently. That is the double-article write `_finish_run` was
    --     made atomic to avoid, arriving through a different door.
    --
    -- A heartbeat separates "slow" from "dead": it asks whether the run has
    -- shown any sign of life recently rather than how long it has been going,
    -- which detects a kill in ~2 minutes and cannot fire on a live run at any
    -- duration.
    --
    -- Deliberately NOT indexed. The `running` set is bounded by the number of
    -- worker processes (one, today), so an index here would serve a sequential
    -- scan over a handful of rows — the same unused-index cost as the ANN
    -- index this schema declines to create. Revisit only if concurrent workers
    -- reach the hundreds.
    heartbeat_at    TIMESTAMPTZ,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

CREATE INDEX pipeline_runs_status_idx ON pipeline_runs (status, created_at DESC);

-- The worker's claim query: oldest queued run whose backoff has elapsed.
CREATE INDEX pipeline_runs_claimable_idx
    ON pipeline_runs (created_at)
    WHERE status = 'queued';

CREATE TABLE pipeline_stage_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        UUID NOT NULL REFERENCES pipeline_runs (id) ON DELETE CASCADE,
    stage         TEXT NOT NULL,      -- matches pipeline.stages.StageName
    ordinal       SMALLINT NOT NULL,
    -- Stage rows are kept per attempt rather than overwritten: diagnosing a
    -- run that succeeded on its third try means seeing what the first two did.
    -- This is why `attempt` is part of the uniqueness below — without it a
    -- retry is an integrity error rather than a second set of rows.
    attempt       SMALLINT NOT NULL DEFAULT 1,
    status        run_status NOT NULL DEFAULT 'queued',
    error         JSONB,
    metrics       JSONB,              -- {candidates: 47, kept: 8, verdict: ...}

    -- The token ledger lives here rather than only on the run because this is
    -- already the right grain — one row per run × attempt × stage, kept per
    -- attempt — and retries are where cost surprises come from.
    --
    -- `model` is provider-namespaced ("anthropic/claude-sonnet-5",
    -- "ollama/llama3.1:8b") and is the join key into app/llm/pricing.py. Bare
    -- names make a local model, which is genuinely free, indistinguishable from
    -- a hosted one nobody has priced yet — and those two must never report the
    -- same cost. NULL for the four stages that call no model; NULL rather than
    -- '' because an empty string is a lookup miss that reads as unpriced.
    --
    -- Written even when the stage FAILED. A rollback discards our writes, not
    -- the provider's charge: a call that answered and then truncated spends the
    -- entire 8K synthesis budget and produces nothing, so a zero here would
    -- make the pipeline look cheapest at the moment it is burning the most.
    model              TEXT,
    input_tokens       INTEGER NOT NULL DEFAULT 0,
    output_tokens      INTEGER NOT NULL DEFAULT 0,
    cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens INTEGER NOT NULL DEFAULT 0,

    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,

    UNIQUE (run_id, attempt, ordinal)
);

ALTER TABLE articles
    ADD CONSTRAINT articles_pipeline_run_fk
    FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs (id) ON DELETE SET NULL;

-- ---------------------------------------------------------------------------
-- Invariant #4, at the storage layer: the AI draft is immutable.
--
-- Application code is expected to never update original_content; this makes
-- that guarantee hold even if it does. Human edits belong in edited_content.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION reject_original_content_update() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.original_content IS DISTINCT FROM OLD.original_content THEN
        RAISE EXCEPTION
            'articles.original_content is immutable (article %); write human edits to edited_content',
            OLD.id
            USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER articles_original_content_immutable
    BEFORE UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION reject_original_content_update();

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER articles_touch_updated_at
    BEFORE UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

COMMIT;

-- ---------------------------------------------------------------------------
-- v2 (NOT in the MVP) — full text for the 2–3 load-bearing sources a verdict
-- rests on, via Europe PMC open access, chunked into ~500-token passages.
--
-- Sketched here rather than left undesigned so the FK direction and the
-- "abstracts for breadth, full text for the pivotal few" split are settled
-- now. Do not create this table until Phase 5+ — and when you do, it goes in a
-- new migration file, not here.
--
-- CREATE TABLE source_passages (
--     id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
--     source_id    UUID NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
--     ordinal      SMALLINT NOT NULL,
--     section      TEXT,                       -- 'methods' | 'results' | ...
--     content      TEXT NOT NULL,
--     token_count  SMALLINT NOT NULL,
--     embedding    vector(:embedding_dim),
--     UNIQUE (source_id, ordinal)
-- );
--
-- This is the table an HNSW index would actually serve — a passage search that
-- issues `ORDER BY embedding <=> $1 LIMIT k` with no restrictive filter, which
-- is the query shape `sources` never has. See the note in the `sources`
-- section above.
--
-- CREATE INDEX source_passages_embedding_hnsw
--     ON source_passages USING hnsw (embedding vector_cosine_ops);
-- ---------------------------------------------------------------------------
