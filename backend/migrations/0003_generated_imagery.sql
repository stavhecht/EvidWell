-- Generated article imagery — the pipeline's two frames, and the pairing that
-- keeps the feed tile honest.
--
-- Apply with:  python -m scripts.migrate
--
-- 1. Only one of the two pictures needs schema, and it is not the one in the
--    article. ILLUSTRATE draws a landscape frame and PersistStage writes it
--    into `original_content` as an ordinary `image` node, so it is resized,
--    re-wrapped, replaced and removed exactly like a picture a reviewer
--    uploaded, and `assert_media_is_ours` walks it on every autosave. Nothing
--    about that half needs a column.
--
-- 2. This column exists for the *other* frame. Every tile shape in
--    `tileRatio()` is 1:1 or taller, and `object-cover` fits a landscape
--    picture to a portrait tile by throwing the sides away — so the stage also
--    draws a portrait version from the same prompt and the same seed. That one
--    is deliberately not in the document: a picture that never appears in the
--    article has no business being editable as though it did, and a reviewer
--    deleting a block they cannot see is a bug report.
--
-- 3. Which creates the one thing to be careful about. The rule is that a feed
--    tile cannot show a picture the article does not contain, and a portrait
--    frame held on the row is exactly such a picture. What keeps the rule true
--    is the pairing: `derive_card` uses `cover` **only while `lead.src` is
--    still the document's own first image**. Replace that picture, delete it,
--    or place another above it, and the cover is dropped and the card falls
--    back to ordinary derivation from the body. So the tile is never something
--    the reviewer did not approve inside the article — it is the other framing
--    of the thing they did.
--
-- 4. JSONB rather than two TEXT columns because the prompt, model and seed
--    belong with the paths. The console's regenerate action runs outside any
--    pipeline run, so `pipeline_stage_runs.metrics` is not available to it —
--    this row is the only place that provenance can live for a picture drawn
--    after the run finished. "Which words produced this image" stops being
--    answerable the moment `imagery/prompt.py` changes, and provenance
--    outranks tidiness.
--
-- 5. NULL is the common case three times over: every article written before
--    this migration, every run with no IMAGE_GEN_KEY, and every run whose
--    generation failed — which never fails the run.
--
-- No index: this column is never a predicate, only a payload. No CHECK tying
-- cover to lead: the pair is atomic in `services/illustration.py` and the
-- pairing is enforced in `derive_card` where it can also see the document,
-- which a constraint cannot.
--
-- NOTE ON `pipeline_stage_runs.ordinal`: ILLUSTRATE was inserted at position 4,
-- so runs recorded before this carry `validate` at 4 and `persist` at 5 rather
-- than 5 and 6. Nothing joins the number to the name — it only orders one run's
-- stages for display — and those rows are correct about the pipeline they ran
-- on. Deliberately not backfilled.

BEGIN;

ALTER TABLE articles
    ADD COLUMN generated_imagery jsonb;

COMMENT ON COLUMN articles.generated_imagery IS
    'Both frames of the generated illustration plus the prompt, model and seed '
    'that made them. `lead` is also an image node in original_content; `cover` '
    'is the portrait framing for the feed tile and lives only here. The cover '
    'reaches card_image only while lead.src is still the document''s first '
    'image — see services/card.py::derive_card.';

COMMIT;
