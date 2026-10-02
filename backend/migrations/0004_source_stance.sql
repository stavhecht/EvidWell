-- Which way each source points, per claim.
--
-- The verdict scale measures support, and the verdict cap counted a cited
-- source toward a claim by what it was *retrieved for*, never by what it
-- *found*. A trial that tested a claim and found nothing was therefore
-- indistinguishable, everywhere downstream, from one that bore it out — and on
-- 2026-10-02 eight drafts refuting a claim read `no_evidence` while citing the
-- very trials that refuted it.
--
-- The APPRAISE stage (app/pipeline/steps/appraise.py) now labels each ranked
-- source against each claim it was retrieved for. `article_sources` is already
-- keyed (article_id, source_id, claim), which is exactly the grain a label
-- belongs to, so it is one column.
--
-- NULL means **not appraised**: an article from before the stage, a run with
-- appraisal switched off, a model call that failed, or a handle the model
-- skipped. It never means `unclear`, which is an answer the model gave. Like
-- `retraction_checked_at IS NULL`, nobody asked is not the same as clean.
--
-- Nothing reads this to gate a verdict yet. It is recorded and shown in the
-- review desk while its accuracy is measured (see CLAUDE.md, APPRAISE).

BEGIN;

CREATE TYPE source_stance AS ENUM (
    'supports',
    'no_effect',
    'contradicts',
    'unclear',
    'off_topic'
);

ALTER TABLE article_sources ADD COLUMN stance source_stance;

COMMIT;
