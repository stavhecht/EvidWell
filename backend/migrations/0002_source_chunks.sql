-- 0002 — abstracts are embedded as overlapping chunks, not as one vector each.
--
-- app/retrieval/chunking.py splits each abstract into 120-word chunks that
-- share 30 words with their neighbour, and every chunk gets its own vector
-- here. A paper's similarity to a claim is its best chunk's similarity
-- (app/retrieval/rerank.py).
--
-- `sources.embedding` is dropped, because the vectors now live in this table.
-- `sources.embedding_model` stays, and now records which model embedded the
-- paper's chunks. It is reset to NULL so that every cached paper counts as not
-- yet embedded: the next run that retrieves a paper chunks it, and
--
--     python -m scripts.reembed_sources --apply
--
-- chunks everything else. Until that runs, a paper with no chunks is left out
-- of ranking rather than scored as zero.
--
-- No ANN (HNSW) index, for the same reason `sources.embedding` never had one:
-- ranking reads one run's candidates by source_id and scores all of them
-- exactly, so an approximate index could never be chosen. The primary key's
-- leading `source_id` column is the index that lookup uses.

BEGIN;

CREATE TABLE source_chunks (
    source_id  UUID     NOT NULL REFERENCES sources (id) ON DELETE CASCADE,
    ordinal    SMALLINT NOT NULL,          -- 0, 1, 2… in reading order
    content    TEXT     NOT NULL,
    embedding  vector(:embedding_dim) NOT NULL,
    PRIMARY KEY (source_id, ordinal)
);

ALTER TABLE sources DROP COLUMN embedding;
UPDATE sources SET embedding_model = NULL;

COMMIT;
