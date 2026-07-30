-- EvidWell initial schema
--
-- Apply with:  psql "$DATABASE_URL" -v embedding_dim=1024 -f migrations/0001_initial.sql
--
-- The embedding dimension is templated because changing it later is a table
-- migration plus an index rebuild, not a config change. It must match
-- settings.embedding_dim in the application config.

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
CREATE TYPE study_type AS ENUM (
    'unknown',
    'in_vitro',
    'animal',
    'case_report',
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

    -- Invariant #2 depends on this: a source with neither identifier can never
    -- satisfy "resolves to a real PMID/DOI", so it must not exist.
    CONSTRAINT sources_identifier_required CHECK (pmid IS NOT NULL OR doi IS NOT NULL)
);

-- Partial unique indexes make the cache upsert a single ON CONFLICT rather
-- than a read-then-write race between concurrent retrieval fan-outs.
CREATE UNIQUE INDEX sources_doi_key  ON sources (lower(doi)) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX sources_pmid_key ON sources (pmid)       WHERE pmid IS NOT NULL;

-- Cosine similarity over abstract embeddings. m/ef_construction are starting
-- values; ef_search is set per-session at query time in retrieval/rerank.py.
CREATE INDEX sources_embedding_hnsw
    ON sources USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

CREATE INDEX sources_study_type_year_idx ON sources (study_type, year DESC);

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
-- pipeline_runs / pipeline_stage_runs — observability.
--
-- Without these, a draft that fails citation validation looks identical to a
-- pipeline that never ran. Each stage row maps 1:1 onto a future Step
-- Functions state.
-- ---------------------------------------------------------------------------

CREATE TABLE pipeline_runs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    topic           TEXT NOT NULL,
    source_blurb    TEXT,
    status          run_status NOT NULL DEFAULT 'queued',
    article_id      UUID REFERENCES articles (id) ON DELETE SET NULL,
    error           JSONB,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    requested_by    UUID REFERENCES users (id) ON DELETE SET NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ
);

CREATE INDEX pipeline_runs_status_idx ON pipeline_runs (status, created_at DESC);

CREATE TABLE pipeline_stage_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id        UUID NOT NULL REFERENCES pipeline_runs (id) ON DELETE CASCADE,
    stage         TEXT NOT NULL,      -- matches pipeline.stages.StageName
    ordinal       SMALLINT NOT NULL,
    status        run_status NOT NULL DEFAULT 'queued',
    error         JSONB,
    metrics       JSONB,              -- {candidates: 47, kept: 8, tokens: {...}}
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,

    UNIQUE (run_id, ordinal)
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
-- now. Do not create this table until Phase 5+.
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
-- CREATE INDEX source_passages_embedding_hnsw
--     ON source_passages USING hnsw (embedding vector_cosine_ops);
-- ---------------------------------------------------------------------------
