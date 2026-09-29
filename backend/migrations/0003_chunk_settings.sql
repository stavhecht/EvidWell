-- 0003 — record how each paper's chunks were cut, so a new chunk size re-chunks.
--
-- Until now a paper counted as embedded when `sources.embedding_model` matched
-- the live model. That misses a change to the chunk size itself: a paper cut
-- into 120-word chunks would look current forever after the size changed.
--
-- `chunk_settings` holds `CHUNK_SETTINGS` from app/retrieval/chunking.py
-- (e.g. '300/30 words'), written beside `embedding_model` whenever a paper's
-- chunks are written. A paper is current only when both match.
--
-- Every existing row starts NULL, so every cached paper reads as out of date:
-- the next run that retrieves a paper re-chunks it, and
--
--     python -m scripts.reembed_sources --apply
--
-- re-chunks the rest. Their current chunks are left in place and keep being
-- ranked until then, so nothing stops working in between.
--
-- This ships with the chunk size moving from 120 to 300 words: sized to fit
-- the embedding model's 512-token window rather than to force a split, so most
-- abstracts become one chunk again.

BEGIN;

ALTER TABLE sources ADD COLUMN chunk_settings TEXT;

COMMIT;
