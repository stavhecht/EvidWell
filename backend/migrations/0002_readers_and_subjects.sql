-- You.th — reader accounts, saved folders, the contact inbox, and the two
-- article fields the redesigned feed needs.
--
-- Apply with:  python -m scripts.migrate
--
-- Four things land together because they are one feature: the feed is now
-- image-first and browsable by subject, and a reader can keep what they find.
--
--   1. `subject` on articles — the chromatic axis the frontend has carried
--      helpers for since the first design pass but nothing has ever served.
--   2. `card_image` — derived from the approved article, never uploaded
--      separately, so the tile cannot show a picture the article does not.
--   3. `readers` + folders + saves — a reader account, entirely separate from
--      `users`. See the note on that separation below.
--   4. `contact_requests` — the "Let us know" inbox the console reads.

BEGIN;

-- ---------------------------------------------------------------------------
-- Enumerated domains
-- ---------------------------------------------------------------------------

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

-- ---------------------------------------------------------------------------
-- articles — the subject axis and the derived card image
-- ---------------------------------------------------------------------------

ALTER TABLE articles
    -- Nullable on purpose. Every consumer degrades to ink when it is absent,
    -- so a reviewer who has not classified a draft does not block publication,
    -- and back-filling the two pre-existing articles is not a schema concern.
    ADD COLUMN subject        subject,

    -- The first image in the approved body, materialised at publish time
    -- alongside card_headline / card_excerpt, and by exactly the same rule:
    -- derived from what the human approved, never a separate upload and never
    -- a separate model call. A tile therefore cannot show a picture that is
    -- not in the article. NULL is normal — the feed falls back to a
    -- typographic tile rather than a placeholder graphic.
    ADD COLUMN card_image     TEXT,
    ADD COLUMN card_image_alt TEXT;

-- The feed's index scan now also serves `WHERE subject = $1`. Kept as a second
-- index rather than widening articles_feed_idx: the unfiltered feed is the
-- common path and must not pay for the narrow one.
CREATE INDEX articles_subject_feed_idx
    ON articles (subject, published_at DESC, id DESC)
    WHERE status = 'published';

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

COMMIT;
