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
-- This file is a squashed baseline, collapsed four times. The first pass folded
-- in retry bookkeeping, the removal of an unused HNSW index, and run
-- heartbeats. The second (2026-08-21) folded in the `narrative_review` study
-- type and the per-stage token ledger. The third, the same day, folded in
-- retraction tracking. The fourth (2026-09-06) folded in everything that had
-- accumulated as 0002–0006: reader accounts and the contact inbox, the subject
-- axis and the derived card image, generated imagery, the move of media bytes
-- off local disk and into `media_objects`, and trend discovery — including the
-- angle identity 0006 arrived at, so the index it dropped is simply never
-- created here. Each change's reasoning is preserved at the point it applies —
-- beside the column, the enum value, or the absent index it explains — rather
-- than as history at the top of the file.
--
-- **The condition, not the habit.** Squashing is free only while every database
-- holding this schema can be dropped and rebuilt. Nothing is deployed, so that
-- is still true; it stops being true the first time this runs somewhere whose
-- contents are not reproducible. The second squash was not free even here — the
-- local database held 92 cached sources and two articles, and rebuilding it
-- meant a pg_dump of the data, a drop of the volume, and a restore. The fourth
-- was not free either: it discarded a bootstrapped discovery ledger, which is a
-- ~3 minute rebuild (`scan_trends --bootstrap --apply`) and tens of thousands
-- of rows. That is affordable for one developer and for nobody else.
--
-- scripts/migrate.py records a sha256 per filename and refuses to re-run a file
-- whose contents changed, which is what makes squashing a deliberate act rather
-- than an accident. From here every schema change is a new numbered file,
-- starting at 0002.
--
-- ORDERING. Two things move relative to where they were written. The shared
-- trigger functions are defined before the first table that attaches a trigger,
-- rather than after every table, so `readers` can carry its `touch_updated_at`
-- at its own definition site. And the discovery tables sit last, because they
-- carry foreign keys into `users`, `pipeline_runs` and `study_type`.
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

-- What kind of thing an article assesses. Colour in the UI means *subject*,
-- never verdict — see frontend/src/features/evidence/subject.ts. Deliberately
-- a small closed set set by a reviewer at publish time: it cannot be derived
-- from `product`, which is free text, and a guessed subject would put a
-- confident colour on an unchecked classification.
CREATE TYPE subject AS ENUM (
    'supplement',
    'device',
    'protocol',
    'food',
    'topical'
);

CREATE TYPE contact_kind AS ENUM (
    'fact_check',   -- "here is a claim, check it"
    'topic',        -- "cover this"
    'other'
);

CREATE TYPE contact_status AS ENUM (
    'new',
    'answered',
    'closed'
);

-- Trend discovery. Deliberately not reusing `run_status` for a scan: a scan is
-- not a pipeline run — it has no attempts, no heartbeat and no article — and
-- borrowing the type to save four lines would couple two lifecycles that have
-- no reason to change together.
CREATE TYPE discovery_scan_status      AS ENUM ('running', 'succeeded', 'failed');
CREATE TYPE discovery_scan_mode        AS ENUM ('scan', 'bootstrap');
CREATE TYPE discovery_descriptor_kind  AS ENUM ('substance', 'outcome', 'stoplisted');
CREATE TYPE discovery_candidate_status AS ENUM ('proposed', 'promoted', 'dismissed', 'expired');
CREATE TYPE run_origin                 AS ENUM ('console', 'discovery');

-- ---------------------------------------------------------------------------
-- Shared trigger functions
--
-- Defined before the tables that attach them so each table can carry its own
-- triggers at its definition site. `reject_original_content_update` is
-- invariant #4 at the storage layer: application code is expected never to
-- update original_content, and this makes the guarantee hold even if it does.
-- Human edits belong in edited_content.
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

CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------------
-- users — reviewers. Deliberately minimal, but reviewed_by is a real FK so
-- "who approved this" stays answerable.
--
-- `readers` further down is a SEPARATE table, and that separation is
-- load-bearing rather than tidiness. See the note there.
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

    -- The product's one chromatic axis. Nullable on purpose: every consumer
    -- degrades to ink when it is absent, so a reviewer who has not classified a
    -- draft does not block publication. It is set through its own endpoint
    -- rather than the content autosave, and is legal *after* publication,
    -- because it drives a colour and a browse listing rather than a word the
    -- reader was shown.
    subject           subject,

    -- Derived card fields. Materialised at publish time by services/card.py
    -- so the feed query stays a single index scan. Derived, never generated —
    -- card and article cannot contradict each other.
    card_headline     TEXT,
    card_excerpt      TEXT,
    card_verdict      verdict,

    -- The first image in the approved body, materialised at publish time
    -- alongside card_headline / card_excerpt, and by exactly the same rule:
    -- derived from what the human approved, never a separate upload and never
    -- a separate model call. A tile therefore cannot show a picture that is
    -- not in the article. NULL is normal — the feed falls back to a
    -- typographic tile rather than a placeholder graphic.
    card_image        TEXT,
    card_image_alt    TEXT,

    -- Both frames of the pipeline's generated illustration, plus the prompt,
    -- model and seed behind each. Only one of the two pictures needs schema,
    -- and it is not the one in the article: ILLUSTRATE draws a landscape frame
    -- and PersistStage writes it into `original_content` as an ordinary `image`
    -- node, so it is resized, re-wrapped, replaced and removed exactly like a
    -- picture a reviewer uploaded, and `assert_media_is_ours` walks it on every
    -- autosave. Nothing about that half needs a column.
    --
    -- This column exists for the *other* frame. Every tile shape in
    -- `tileRatio()` is 1:1 or taller, and `object-cover` fits a landscape
    -- picture to a portrait tile by throwing the sides away — so the stage also
    -- draws a portrait version. That one is deliberately not in the document: a
    -- picture that never appears in the article has no business being editable
    -- as though it did, and a reviewer deleting a block they cannot see is a
    -- bug report.
    --
    -- Which creates the one thing to be careful about. The rule is that a feed
    -- tile cannot show a picture the article does not contain, and a portrait
    -- frame held on the row is exactly such a picture. What keeps the rule true
    -- is the pairing: `derive_card` uses `cover` **only while `lead.src` is
    -- still the document's own first image**. Replace that picture, delete it,
    -- or place another above it, and the cover is dropped and the card falls
    -- back to ordinary derivation from the body. So the tile is never something
    -- the reviewer did not approve inside the article — it is the other framing
    -- of the thing they did.
    --
    -- JSONB rather than columns because the provenance belongs with the paths,
    -- and it lives per frame: either frame can be redrawn alone through
    -- `POST /console/articles/{id}/illustration`, and a shared prompt would
    -- then be a true record of one picture and a false one of the other. The
    -- console's regenerate action also runs outside any pipeline run, so
    -- `pipeline_stage_runs.metrics` is not available to it — this row is the
    -- only place provenance can live for a picture drawn after the run
    -- finished, and "which words produced this image" stops being answerable
    -- the moment imagery/prompt.py changes.
    --
    -- NULL is the common case three times over: no IMAGE_GEN_KEY, generation
    -- disabled, and generation that failed — which never fails the run.
    --
    -- No index: never a predicate, only a payload. No CHECK tying cover to
    -- lead: the pairing is enforced in `derive_card`, where it can also see the
    -- document, which a constraint cannot.
    generated_imagery JSONB,

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

COMMENT ON COLUMN articles.generated_imagery IS
    'Both frames of the generated illustration, each with the prompt, model and '
    'seed that made it. `lead` is also an image node in original_content; '
    '`cover` is the portrait framing for the feed tile and lives only here. The '
    'cover reaches card_image only while lead.src is still the document''s first '
    'image — see services/card.py::derive_card.';

CREATE UNIQUE INDEX articles_slug_key ON articles (slug);

-- The only index the public feed needs.
CREATE INDEX articles_feed_idx ON articles (published_at DESC) WHERE status = 'published';

-- The feed's index scan for `WHERE subject = $1`. A second index rather than a
-- widening of articles_feed_idx: the unfiltered feed is the common path and
-- must not pay for the narrow one.
CREATE INDEX articles_subject_feed_idx
    ON articles (subject, published_at DESC, id DESC)
    WHERE status = 'published';

-- The only index the review queue needs.
CREATE INDEX articles_queue_idx ON articles (created_at DESC) WHERE status = 'pending_review';

-- Flagged articles are a handful out of the whole table and are queried by
-- their presence, so the index carries only them.
CREATE INDEX articles_retraction_flagged_idx
    ON articles (retraction_flagged_at DESC)
    WHERE retraction_flagged_at IS NOT NULL;

CREATE TRIGGER articles_original_content_immutable
    BEFORE UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION reject_original_content_update();

CREATE TRIGGER articles_touch_updated_at
    BEFORE UPDATE ON articles
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

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

    -- Who asked for this run. Derivable from discovery_candidates.
    -- pipeline_run_id, and derivable state can disagree with its source. It
    -- earns the column anyway: "what has discovery cost us" is the question
    -- that decides whether that feature keeps its cron slot, and with the
    -- column it is one WHERE against the per-stage token ledger below rather
    -- than a join through a table that is about proposals, not spend.
    --
    -- It also keeps promotion honest. The route sets origin and the candidate's
    -- pipeline_run_id in the same transaction, so the two disagreeing is a bug
    -- that can be *found* — where a single source of truth would just be
    -- silently wrong.
    --
    -- NOT NULL DEFAULT 'console' makes it invisible to create_run, which does
    -- not set it.
    origin          run_origin NOT NULL DEFAULT 'console',

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
    -- same cost. NULL for the stages that call no model; NULL rather than ''
    -- because an empty string is a lookup miss that reads as unpriced.
    --
    -- ILLUSTRATE is one of those NULLs on purpose, even though it calls a
    -- model: an image is billed per render and this table prices four token
    -- columns, so a model id written against four zeros is exactly the
    -- "unpriced model reads as free" mistake pricing.py exists to refuse. Its
    -- count, seed, latency and model go into `metrics` instead.
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
-- readers — the public side's accounts
--
-- A separate table from `users`, and that separation is load-bearing rather
-- than tidiness. `users` is the reviewer roster: `articles.reviewed_by` is a
-- real foreign key into it, so a row there is a claim about who is answerable
-- for a published article. Readers sign themselves up. Putting both in one
-- table with a role column means the only thing standing between a
-- self-service signup and the review queue is a correctly-set enum — and the
-- console's auth gate would be one WHERE clause away from admitting anyone
-- who registered. Two tables makes that mistake unrepresentable: a reader id
-- can never satisfy `require_reviewer`, because it is not in `users`.
-- ---------------------------------------------------------------------------

CREATE TABLE readers (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,          -- argon2id, same hasher as users
    display_name  TEXT NOT NULL,

    -- Which subjects this reader wants first. Ordering, never filtering: the
    -- feed moves these to the top and still serves everything else below.
    interests     subject[] NOT NULL DEFAULT '{}',
    newsletter    BOOLEAN NOT NULL DEFAULT FALSE,

    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX readers_email_key ON readers (lower(email));

CREATE TRIGGER readers_touch_updated_at
    BEFORE UPDATE ON readers
    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

CREATE TABLE reader_folders (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reader_id  UUID NOT NULL REFERENCES readers (id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- Lets reader_saves carry a composite FK, which is what makes "a save can
    -- only point at a folder belonging to the same reader" a database fact
    -- rather than something every handler has to remember to check.
    UNIQUE (id, reader_id)
);

CREATE UNIQUE INDEX reader_folders_name_key
    ON reader_folders (reader_id, lower(name));

CREATE TABLE reader_saves (
    reader_id  UUID NOT NULL REFERENCES readers (id) ON DELETE CASCADE,
    article_id UUID NOT NULL REFERENCES articles (id) ON DELETE CASCADE,
    folder_id  UUID NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One article sits in at most one of a reader's folders. Moving it is an
    -- upsert of folder_id, which is what the Save control in the UI does — and
    -- the PK is what stops the same article accumulating a copy per folder.
    PRIMARY KEY (reader_id, article_id),

    FOREIGN KEY (folder_id, reader_id)
        REFERENCES reader_folders (id, reader_id) ON DELETE CASCADE
);

CREATE INDEX reader_saves_folder_idx ON reader_saves (folder_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- contact_requests — "Let us know"
--
-- Unauthenticated writes, reviewer-only reads. No FK to readers: a request is
-- worth having from someone who never signs up, and the email on the row is
-- how we reply either way.
-- ---------------------------------------------------------------------------

CREATE TABLE contact_requests (
    id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind       contact_kind NOT NULL DEFAULT 'other',
    name       TEXT,
    email      TEXT NOT NULL,
    link       TEXT,
    note       TEXT NOT NULL,
    status     contact_status NOT NULL DEFAULT 'new',

    -- Who in the console dealt with it, and when. Same shape as the review
    -- trail on articles, for the same reason: "who handled this" stays
    -- answerable after the person has moved on.
    handled_by UUID REFERENCES users (id) ON DELETE SET NULL,
    handled_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A handled request must name who handled it, and an unhandled one must
    -- not. The same shape as the articles CHECK that carries invariant #1:
    -- a status that claims a human acted has to point at the human.
    CONSTRAINT contact_handled_has_reviewer CHECK (
        (status = 'new' AND handled_by IS NULL AND handled_at IS NULL)
        OR (status <> 'new' AND handled_by IS NOT NULL AND handled_at IS NOT NULL)
    )
);

CREATE INDEX contact_requests_inbox_idx
    ON contact_requests (status, created_at DESC);

-- ---------------------------------------------------------------------------
-- media_objects — content-addressed image bytes.
--
-- WHY THIS TABLE AND NOT A COLUMN ON `articles`
--
-- The obvious shape — the picture lives on the row it illustrates — cannot be
-- built, for three reasons that are each independently fatal:
--
-- 1. ILLUSTRATE is stage 5 and PERSIST is stage 7. The `articles` row does not
--    exist when the pipeline stores its pictures, and PERSIST stays last
--    because its write commits atomically with the run-completion row. A
--    per-article column would mean either reordering the pipeline or holding
--    image bytes in memory across two stages.
-- 2. Reviewer uploads are deliberately not article-scoped — see the docstring
--    on `POST /console/media`. An image is referenced by whichever document
--    embeds it, and a reviewer moves images between drafts.
-- 3. Content addressing dedups across articles. The same picture used twice is
--    one row here and would be N copies on `articles`.
--
-- So the bytes are keyed by their own digest, and nothing about *who
-- references what* is recorded here.
--
-- WHY NOT LOCAL DISK. Bytes lived under `backend/var/media` until that turned
-- out to be the one piece of published state a database backup did not cover:
-- images generated by a host-venv run were served as 404s the moment the stack
-- moved into containers, because the compose volume was its own storage and had
-- never seen that directory. One store, one backup, one restore.
--
-- The URL did not change with the move. `MEDIA_URL_PREFIX/<2 hex>/<62 hex>.<ext>`
-- is still what a document holds and still what `MEDIA_SRC_RE` matches, so
-- `assert_media_is_ours` is untouched. The split after two characters is now
-- decorative — there is no directory to shard — and stays because rewriting
-- stored JSON to drop a slash would be a migration with nothing to gain.
--
-- ON `bytea`. Postgres TOASTs anything over ~2kB out of line automatically, so
-- an 8MB ceiling (`media_max_bytes`) is unremarkable. Compression buys close to
-- nothing — all four accepted formats are already compressed — so the storage
-- cost is the file size plus change.
--
-- No `article_id`, no index beyond the primary key: this table is only ever
-- read by exact digest, from the route that serves it.
--
-- ORPHANS are deferred. An image dropped from a draft leaves its row behind. The
-- sweep is a DELETE against a scan of the document columns, with no filesystem
-- to keep in step — but it is still not this file's job.
-- ---------------------------------------------------------------------------

CREATE TABLE media_objects (
    -- The SHA-256 of `data`, lowercase hex. Not a surrogate key: it is what the
    -- URL contains and what makes the same bytes stored twice one row.
    digest      text PRIMARY KEY,
    -- Decided by sniffing the leading bytes, never by anything a client said.
    -- Constrained to the four formats a browser renders without executing
    -- anything; SVG is absent on purpose (see services/media.py).
    extension   text        NOT NULL,
    data        bytea       NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),

    -- Mirrors the shape half of MEDIA_SRC_RE. The regex is what keeps a bad
    -- path out of a document; this is what keeps a bad row out of the table,
    -- so a digest can never be assembled into a URL the checker would refuse.
    CONSTRAINT media_objects_digest_is_sha256
        CHECK (digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT media_objects_extension_is_supported
        CHECK (extension IN ('png', 'jpg', 'gif', 'webp'))
);

COMMENT ON TABLE media_objects IS
    'Content-addressed image bytes for article media, keyed by SHA-256. Serves '
    'both reviewer uploads and pipeline-generated illustrations. Referenced '
    'only by URL from inside article documents — there is deliberately no FK '
    'in either direction, because an image outlives the draft that embedded it.';

-- ---------------------------------------------------------------------------
-- Trend discovery. A periodic scan of newly-indexed PubMed literature proposes
-- emerging substances to a reviewer, who decides which become articles.
--
-- After a rebuild:  python -m scripts.scan_trends --bootstrap --apply  (once, ~3 min)
--
-- WHY A LEDGER AND NOT COUNTERS
--
-- The obvious shape is `discovery_counts (descriptor_ui, window, n)` incremented
-- per scan. It cannot work, because MeSH indexing lags PubMed entry by days to
-- weeks: a record inside last fortnight's dates only becomes visible during this
-- one, so every scan must re-read a window it has already counted. With counters
-- that re-read is double counting, and the only defence is arithmetic about
-- which scan saw what — which breaks the first time a cron slot is missed.
--
-- `discovery_observations` stores one row per (descriptor, paper) instead, and
-- every count is a COUNT(DISTINCT pmid) over it. The composite primary key makes
-- re-reading a no-op, which buys three things at once: the overlap is free, a
-- missed scan self-heals on the next run, and the whole script is idempotent —
-- running it twice in a row changes nothing.
--
-- Records are bucketed by `entrez_date` (when PubMed received the record), not
-- by which scan found them, so a paper indexed late still lands in the window it
-- belongs to rather than inflating the window that happened to notice it.
--
-- WHY THE DESCRIPTOR IS THE IDENTITY
--
-- `articles.product` is free text written by the extraction model, so dedup
-- against it is fuzzy string matching: "Creatine Monohydrate Powder" and
-- "creatine" are the same substance and no index can say so. MeSH descriptor UIs
-- are canonical (D003401 *is* creatine, in every record, forever), which is what
-- makes `discovery_candidates_one_live_per_angle` below a real guarantee rather
-- than a best effort. It is the reason this feature counts MeSH tags instead of
-- reading titles.
--
-- WHAT THIS DOES NOT DO
--
-- It does not enqueue anything. `discovery_candidates` rows are proposals; the
-- transition to a `pipeline_runs` row happens in the console, under a reviewer,
-- through the same insert `create_run` uses. Invariant #1 says a human decides
-- what the public sees; this table is that decision moved one step earlier, to
-- what gets written at all.
-- ---------------------------------------------------------------------------

-- ---------------------------------------------------------------------------
-- discovery_scans — one row per invocation of the scan.
-- ---------------------------------------------------------------------------

CREATE TABLE discovery_scans (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),

    -- The edat range this scan asked PubMed for, overlap included. Successive
    -- scans overlap heavily by design; see the header.
    window_start       DATE NOT NULL,
    window_end         DATE NOT NULL,

    mode               discovery_scan_mode   NOT NULL DEFAULT 'scan',
    status             discovery_scan_status NOT NULL DEFAULT 'running',

    seeds_queried      INTEGER NOT NULL DEFAULT 0,
    records_seen       INTEGER NOT NULL DEFAULT 0,
    descriptors_seen   INTEGER NOT NULL DEFAULT 0,
    candidates_emitted INTEGER NOT NULL DEFAULT 0,

    error              JSONB,
    started_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at        TIMESTAMPTZ,

    CONSTRAINT discovery_scans_window_is_ordered CHECK (window_start <= window_end)
);

-- "Where did the last good scan get to" — the only question asked of this
-- table, and the input to the next scan's window. A failed or half-finished
-- scan must not move the mark, hence the partial index.
CREATE INDEX discovery_scans_recent_idx
    ON discovery_scans (window_end DESC)
    WHERE status = 'succeeded';

COMMENT ON TABLE discovery_scans IS
    'One trend scan. Bookkeeping only — the observations it recorded outlive it, '
    'because they are the baseline every later scan is measured against.';

-- ---------------------------------------------------------------------------
-- discovery_descriptors — the MeSH vocabulary we have actually seen.
-- ---------------------------------------------------------------------------

CREATE TABLE discovery_descriptors (
    -- MeSH descriptor unique identifier, e.g. 'D003401'. Names drift between
    -- MeSH editions and UIs do not, so everything keys on this.
    ui            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,

    -- Recomputed every scan rather than frozen, so a misclassification is
    -- fixable with one UPDATE instead of a code change plus a full re-scan.
    -- 'stoplisted' is a real state, not an absence: it records that we saw the
    -- descriptor and decided it carries no signal.
    kind          discovery_descriptor_kind NOT NULL,

    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT discovery_descriptors_ui_is_mesh CHECK (ui ~ '^[DCQ][0-9]{6,9}$')
);

COMMENT ON TABLE discovery_descriptors IS
    'MeSH descriptors observed by trend discovery, with our classification of '
    'each as an intervention, an outcome, or noise. Exists so a label is stored '
    'once rather than on every observation.';

-- ---------------------------------------------------------------------------
-- discovery_observations — the ledger. One row per (descriptor, paper).
-- ---------------------------------------------------------------------------

CREATE TABLE discovery_observations (
    descriptor_ui          TEXT NOT NULL REFERENCES discovery_descriptors (ui),
    pmid                   TEXT NOT NULL,

    -- <PubMedPubDate PubStatus="entrez">: when PubMed received the record. The
    -- bucketing key, and deliberately not the scan date — a paper indexed late
    -- belongs to the window it entered in, not the window that noticed it.
    entrez_date            DATE NOT NULL,

    -- Denormalised from discovery_descriptors.kind at write time. Redundant on
    -- purpose: the baseline query filters on it, and joining a 30k-row scan back
    -- to the vocabulary for a boolean is the difference between an index-only
    -- scan and a hash join per window.
    is_substance           BOOLEAN NOT NULL,

    major_topic            BOOLEAN NOT NULL DEFAULT FALSE,

    -- Whether this paper tagged the descriptor with an intervention qualifier
    -- (/administration & dosage, /therapeutic use, /pharmacology, /adverse
    -- effects). Kept per observation rather than aggregated, because the
    -- substance-vs-biomarker rule is a ratio over papers and the threshold is
    -- expected to be retuned against real data.
    intervention_qualifier BOOLEAN NOT NULL DEFAULT FALSE,

    -- From the same classifier the pipeline uses, over <PublicationTypeList>.
    -- Feeds the quality weight in the score.
    study_type             study_type NOT NULL DEFAULT 'unknown',

    -- The scan that FIRST saw this pair. SET NULL, not CASCADE: observations are
    -- the baseline and must outlive the scan that recorded them — cascading here
    -- would let deleting one scan row silently rewrite history for every
    -- descriptor it touched, and the resulting hole reads as a surge.
    scan_id                UUID REFERENCES discovery_scans (id) ON DELETE SET NULL,

    first_seen_at          TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- This is the dedup. Writes are ON CONFLICT DO NOTHING, which is what makes
    -- re-reading an overlapping window free and the script idempotent.
    PRIMARY KEY (descriptor_ui, pmid)
);

-- The only read pattern: count distinct papers for one substance within a date
-- bucket, repeated across the baseline windows. Partial because outcomes are
-- ~80% of the rows and are never counted this way — they are only ever looked up
-- by pmid to name a topic, which the primary key already serves.
CREATE INDEX discovery_observations_window_idx
    ON discovery_observations (descriptor_ui, entrez_date)
    WHERE is_substance;

COMMENT ON TABLE discovery_observations IS
    'One row per (MeSH descriptor, paper). Every trend count is a COUNT(DISTINCT '
    'pmid) over this table, never an incremented counter — which is what lets '
    'successive scans re-read overlapping windows without double counting.';

-- ---------------------------------------------------------------------------
-- discovery_candidates — what a reviewer is actually shown.
-- ---------------------------------------------------------------------------

CREATE TABLE discovery_candidates (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scan_id         UUID NOT NULL REFERENCES discovery_scans (id) ON DELETE CASCADE,

    substance_ui    TEXT NOT NULL REFERENCES discovery_descriptors (ui),
    -- Nullable: a substance whose co-occurring descriptors name no recognisable
    -- outcome still gets proposed, under a bare topic. Missing context is not a
    -- reason to hide an emerging trend.
    outcome_ui      TEXT REFERENCES discovery_descriptors (ui),

    -- Verbatim what goes into pipeline_runs.topic on promotion. Stored rather
    -- than recomposed at promote time so what the reviewer read is what runs —
    -- the composer's vocabulary changes, and a topic that shifted between the
    -- screen and the queue would be unattributable.
    topic           TEXT NOT NULL,

    score           DOUBLE PRECISION NOT NULL,
    paper_count     INTEGER NOT NULL,
    baseline_count  DOUBLE PRECISION NOT NULL,

    -- {lift, study_mix, top_pmids, window}. The reviewer's evidence for the
    -- proposal and the dismissal rule's memory of what was known at the time.
    rationale       JSONB NOT NULL,

    status          discovery_candidate_status NOT NULL DEFAULT 'proposed',

    -- The run a reviewer created from this candidate. SET NULL so run cleanup
    -- cannot delete the record that a proposal was acted on.
    pipeline_run_id UUID REFERENCES pipeline_runs (id) ON DELETE SET NULL,

    -- SET NULL, matching articles.reviewed_by. Note the same tension that
    -- column has: nulling this on a decided row violates the CHECK below, so a
    -- reviewer who has decided anything cannot be hard-deleted. That is the
    -- intended outcome rather than an oversight — provenance outranks tidiness,
    -- and `users.is_active` is how a reviewer is retired.
    decided_by      UUID REFERENCES users (id) ON DELETE SET NULL,
    decided_at      TIMESTAMPTZ,
    dismiss_reason  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- The same house rule as articles_published_requires_reviewer: a decision
    -- that costs money, or that someone is answerable for, cannot be recorded
    -- without the human attached. No application bug can produce a promoted
    -- candidate with no run, or a dismissal with nobody behind it.
    CONSTRAINT discovery_candidates_promoted_has_run CHECK (
        status <> 'promoted' OR pipeline_run_id IS NOT NULL
    ),
    CONSTRAINT discovery_candidates_decided_has_reviewer CHECK (
        status IN ('proposed', 'expired')
        OR (decided_by IS NOT NULL AND decided_at IS NOT NULL)
    ),
    CONSTRAINT discovery_candidates_dismissed_has_reason CHECK (
        status <> 'dismissed' OR dismiss_reason IS NOT NULL
    )
);

-- THE ANTI-REPEAT GUARANTEE.
--
-- A trend does not emerge and then stop: the same substance will clear the bar
-- again next scan, and the scan after that. Without this index each one appends
-- a row and the desk fills with the same proposal at three different scores.
--
-- The scan therefore writes ON CONFLICT (substance_ui, coalesce(outcome_ui, ''))
-- WHERE status = 'proposed' DO UPDATE, so a re-detected trend *refreshes* its
-- existing row — new score, new counts, same id, same position in the
-- reviewer's list.
--
-- Partial on 'proposed' so history is unconstrained: an angle may have been
-- dismissed in March, promoted in June and be live again now, and all three rows
-- coexist. That history is what suppression_reason() reads.
--
-- THE IDENTITY IS THE (SUBSTANCE, ANGLE) PAIR, NOT THE SUBSTANCE. One live
-- proposal per substance was right for the question the scan asks ("is this
-- substance trending?") and wrong for the article it produces. "Omega-3 for
-- muscle recovery" and "omega-3 for skin elasticity" are not the same piece:
-- they rest on different papers, reach different verdicts, and a reader looking
-- for one is not served by the other. Under a per-substance index, promoting
-- either one silenced the substance entirely and the other angle was never
-- offered. Measured on the live corpus (2026-09-06, 35-day window): omega-3 had
-- 8 papers spread across 55 distinct outcome descriptors, 3 of which carried 4+
-- papers of their own; vitamin D had 9 such outcomes. The angles are really
-- there.
--
-- Two things stop that becoming "one substance, eight ways", and they are
-- deliberately in different places:
--
--   * `discovery_max_angles_per_substance` caps how many angles one scan may
--     emit for one substance. A ceiling on *exposure*, tunable per deployment.
--   * `suppression_reason` collapses near-duplicate outcomes by their spoken
--     keyword rather than their UI, so dismissing "omega-3 for Skin Aging" also
--     holds back "omega-3 for Skin Physiological Phenomena". MeSH is granular
--     enough that a per-UI rule would let the same angle nag under six names.
--
-- The structural guarantee lives here; the editorial one lives in Python. The
-- index refuses a duplicate row, the rule decides what counts as the same
-- question.
--
-- ON coalesce(outcome_ui, ''). `outcome_ui` is nullable — a substance whose
-- co-occurring descriptors name no usable outcome is still proposed under a bare
-- topic. A plain UNIQUE over (substance_ui, outcome_ui) would not constrain
-- those at all, because NULL is never equal to NULL in an index, so a substance
-- could accumulate unlimited bare proposals. Coalescing to the empty string
-- makes "no angle" a value like any other, and exactly one of them may be live.
CREATE UNIQUE INDEX discovery_candidates_one_live_per_angle
    ON discovery_candidates (substance_ui, coalesce(outcome_ui, ''))
    WHERE status = 'proposed';

-- Suppression looks up "what was decided about this angle, most recently".
-- substance_ui is the leading column so the broader question — "anything decided
-- about this substance at all" — is served by the same index.
CREATE INDEX discovery_candidates_decided_idx
    ON discovery_candidates (substance_ui, outcome_ui, decided_at DESC)
    WHERE status IN ('promoted', 'dismissed');

-- The console's list: live proposals, best first.
CREATE INDEX discovery_candidates_desk_idx
    ON discovery_candidates (score DESC)
    WHERE status = 'proposed';

COMMENT ON TABLE discovery_candidates IS
    'A proposed article topic, ranked by how fast its literature is growing. A '
    'proposal only — promotion to a pipeline run is a reviewer action, and '
    'nothing here spends money on its own.';

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
