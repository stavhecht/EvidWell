-- Subject becomes an editorial category, not a kind of object.
--
-- The old set (supplement, device, protocol, food, topical) named what an
-- article assessed. The new set names the area of the publication it is filed
-- under, and it is the feed's browse axis: the drawer narrows the feed to one
-- category (`WHERE subject = $1`, served by articles_subject_feed_idx) and a
-- reader's interests lift categories to the top.
--
-- `other` is a reviewer's answer ("none of these fit"); NULL still means
-- nobody has classified the article.
--
-- Postgres cannot drop enum values, so the type is swapped: rename the old
-- one, create the new one, convert both columns that use it, drop the old.
--
-- Old values are carried over only where the mapping is not a guess:
-- supplement -> supplements and food -> nutrition. device, protocol and
-- topical become NULL (unclassified) rather than being pushed into a category
-- a reviewer never chose — a guessed category puts a confident colour on an
-- unchecked classification. On 2026-10-01 the only classified article was
-- `supplement`, so nothing was actually cleared.

BEGIN;

ALTER TYPE subject RENAME TO subject_old;

CREATE TYPE subject AS ENUM (
    'fitness',
    'nutrition',
    'supplements',
    'sleep_recovery',
    'lifestyle',
    'preventive_health',
    'general_health',
    'wellness',
    'other'
);

-- ALTER COLUMN ... USING cannot hold a subquery, which the array conversion
-- needs, so the mapping lives in two throwaway functions.
CREATE FUNCTION migrate_0002_subject(old subject_old) RETURNS subject
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE old::text
        WHEN 'supplement' THEN 'supplements'::subject
        WHEN 'food'       THEN 'nutrition'::subject
    END
$$;

CREATE FUNCTION migrate_0002_subjects(old subject_old[]) RETURNS subject[]
LANGUAGE sql IMMUTABLE AS $$
    SELECT COALESCE(
        array_agg(DISTINCT mapped) FILTER (WHERE mapped IS NOT NULL),
        '{}'
    )
    FROM (SELECT migrate_0002_subject(o) AS mapped FROM unnest(old) AS o) AS m
$$;

ALTER TABLE articles
    ALTER COLUMN subject TYPE subject USING migrate_0002_subject(subject);

-- The default is typed against the old enum and has to come off first.
ALTER TABLE readers ALTER COLUMN interests DROP DEFAULT;
ALTER TABLE readers
    ALTER COLUMN interests TYPE subject[] USING migrate_0002_subjects(interests);
ALTER TABLE readers ALTER COLUMN interests SET DEFAULT '{}';

DROP FUNCTION migrate_0002_subjects(subject_old[]);
DROP FUNCTION migrate_0002_subject(subject_old);
DROP TYPE subject_old;

COMMIT;
