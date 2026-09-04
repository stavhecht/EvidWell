-- Media bytes move from local disk into the database.
--
-- Apply with:  python -m scripts.migrate
-- Then:        python -m scripts.import_media --apply
--
-- WHY THIS TABLE AND NOT A COLUMN ON `articles`
--
-- The obvious shape — the picture lives on the row it illustrates — cannot be
-- built here, for three reasons that are each independently fatal:
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
-- So the bytes are keyed by their own digest, exactly as they were keyed by it
-- on disk, and nothing about *who references what* is recorded here. That is
-- the same non-relationship the filesystem had, which is why no stored
-- document has to change.
--
-- WHAT DOES NOT CHANGE
--
-- The URL. `MEDIA_URL_PREFIX/<2 hex>/<62 hex>.<ext>` is still what a document
-- holds and still what `MEDIA_SRC_RE` matches, so every path already sitting in
-- `original_content`, `edited_content`, `card_image` and `generated_imagery`
-- keeps working verbatim and `assert_media_is_ours` is untouched. The split
-- after two characters is now decorative — there is no directory to shard — but
-- it stays because rewriting stored JSON to drop a slash would be a migration
-- with nothing to gain from it.
--
-- ON `bytea`
--
-- Postgres TOASTs anything over ~2kB out of line automatically, so an 8MB
-- ceiling (`media_max_bytes`) is unremarkable. Compression buys close to
-- nothing — all four accepted formats are already compressed — so the storage
-- cost is the file size plus change, which is what it was on disk.
--
-- No `article_id`, no index beyond the primary key: this table is only ever
-- read by exact digest, from the route that serves it.
--
-- ORPHANS. Unchanged, and still deferred. An image dropped from a draft leaves
-- its row behind exactly as it left its file behind. The sweep is now a much
-- easier job than it was — a DELETE against a scan of the document columns,
-- with no filesystem to keep in step — but it is still not this migration's.

BEGIN;

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

COMMIT;
