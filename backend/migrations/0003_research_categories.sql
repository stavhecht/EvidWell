-- Research categories become the article categories (0002).
--
-- The research agent had its own list, with exercise, sleep and recovery as
-- separate categories and no `other`. It now uses the article list, so the
-- desk's trending-topics filter and the feed drawer offer the same nine.
-- `research_candidates.category` is TEXT, so this is a data rewrite only:
--   exercise          -> fitness
--   sleep, recovery   -> sleep_recovery
-- Every other old value already exists under the same name.
--
-- `research_runs.config` is rewritten too. It is parsed back into
-- `ResearchConfig` when the worker claims a run, so a run requeued with an old
-- category in it would fail validation at claim time instead of running.

BEGIN;

UPDATE research_candidates
SET category = CASE category
    WHEN 'exercise' THEN 'fitness'
    WHEN 'sleep'    THEN 'sleep_recovery'
    WHEN 'recovery' THEN 'sleep_recovery'
END
WHERE category IN ('exercise', 'sleep', 'recovery');

UPDATE research_runs
SET config = jsonb_set(
    config,
    '{categories}',
    (
        SELECT COALESCE(jsonb_agg(DISTINCT mapped), '[]'::jsonb)
        FROM (
            SELECT CASE value
                WHEN 'exercise' THEN 'fitness'
                WHEN 'sleep'    THEN 'sleep_recovery'
                WHEN 'recovery' THEN 'sleep_recovery'
                ELSE value
            END AS mapped
            FROM jsonb_array_elements_text(config -> 'categories')
        ) AS m
    )
)
WHERE jsonb_typeof(config -> 'categories') = 'array'
  AND config -> 'categories' ?| ARRAY['exercise', 'sleep', 'recovery'];

COMMIT;
