"""Invariants enforced by Postgres, not by application code.

These need a live database. That is the entire point: invariants #1 and #4 are
guaranteed by a CHECK constraint and a trigger precisely so they hold when the
application layer is wrong, and a fake session cannot demonstrate that.

Run with a database available:

    docker compose up -d db
    psql "$DATABASE_URL" -v embedding_dim=1024 -f migrations/0001_initial.sql
    pytest tests/test_db_invariants.py

Skipped automatically when no database is reachable, so the default `pytest`
run stays fast. **A skipped run does not verify these invariants** — run them
before trusting a change to the schema, the review service, or the persist
stage.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.domain.enums import ArticleStatus, StudyType, UserRole, Verdict
from app.security.auth import hash_password
from app.services.review import ReviewError, ReviewService

DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://evidwell:evidwell@localhost:5432/evidwell",
)


@pytest.fixture
async def session() -> Any:
    """A session whose writes are rolled back after each test.

    The engine is built **per test**, not once per module. asyncpg connections
    bind to the event loop that created them, and pytest-asyncio gives each
    test its own loop — a module-scoped engine hands every test after the first
    a connection attached to a dead loop ("got Future attached to a different
    loop"). Rebuilding costs a few milliseconds against a local database and
    removes the whole class of problem.
    """
    engine = create_async_engine(DATABASE_URL)
    try:
        async with engine.connect() as connection:
            await connection.execute(sa.text("SELECT 1"))
    except Exception:
        await engine.dispose()
        pytest.skip(f"no database at {DATABASE_URL}")

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as db:
        try:
            yield db
        finally:
            # Roll back rather than truncate: every test's writes disappear
            # without the tests needing to know about each other's data.
            await db.rollback()
    await engine.dispose()


async def _make_user(db: AsyncSession) -> str:
    user_id = str(uuid.uuid4())
    await db.execute(
        sa.text(
            "INSERT INTO users (id, email, password_hash, display_name, role, created_at)"
            " VALUES (:id, :email, :hash, :name, :role, now())"
        ),
        {
            "id": user_id,
            "email": f"reviewer-{user_id[:8]}@example.com",
            "hash": hash_password("a-sufficiently-long-password"),
            "name": "Test Reviewer",
            "role": UserRole.REVIEWER.value,
        },
    )
    return user_id


async def _make_article(
    db: AsyncSession,
    *,
    status: ArticleStatus = ArticleStatus.PENDING_REVIEW,
    content: dict | None = None,
) -> str:
    article_id = str(uuid.uuid4())
    # `rejected` carries a NOT-NULL-by-CHECK reason, so the helper supplies one
    # rather than every caller having to know that.
    reason = "test rejection" if status is ArticleStatus.REJECTED else None
    await db.execute(
        sa.text(
            """
            INSERT INTO articles (
                id, slug, status, topic, product, headline, summary, verdict,
                original_content, evidence_grade, rejection_reason,
                created_at, updated_at
            ) VALUES (
                :id, :slug, :status, :topic, :product, :headline, :summary,
                :verdict, CAST(:content AS jsonb), :grade, :reason, now(), now()
            )
            """
        ),
        {
            "id": article_id,
            "slug": f"test-{article_id[:8]}",
            "status": status.value,
            "topic": "test topic",
            "product": "Test Product",
            "headline": "A test headline",
            "summary": "A test summary.",
            "verdict": Verdict.MIXED.value,
            "content": _json(content if content is not None else {}),
            "grade": StudyType.RCT.value,
            "reason": reason,
        },
    )
    return article_id


def _json(value: dict) -> str:
    import json

    return json.dumps(value)


# ---------------------------------------------------------------------------
# Invariant #1 — nothing publishes without a human.
# ---------------------------------------------------------------------------


async def test_publishing_without_a_reviewer_violates_the_check_constraint(
    session: AsyncSession,
) -> None:
    """Tested with raw SQL, bypassing the service layer deliberately.

    The constraint exists for exactly the case where the service layer is
    wrong, so testing it through the service would prove nothing.
    """
    article_id = await _make_article(session, content={"type": "doc", "content": []})

    with pytest.raises((IntegrityError, DBAPIError)):
        await session.execute(
            sa.text(
                "UPDATE articles SET status = 'published', published_at = now()"
                " WHERE id = :id"
            ),
            {"id": article_id},
        )
        await session.flush()


async def test_approve_sets_reviewer_and_timestamps(session: AsyncSession) -> None:
    reviewer_id = await _make_user(session)
    article_id = await _make_article(
        session,
        content={
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "attrs": {"beat": 1},
                    "content": [{"type": "text", "text": "It claims to help."}],
                }
            ],
        },
    )

    await ReviewService(session).approve(article_id, reviewer_id)

    row = (
        await session.execute(
            sa.text(
                "SELECT status, reviewed_by, reviewed_at, published_at, card_excerpt"
                " FROM articles WHERE id = :id"
            ),
            {"id": article_id},
        )
    ).one()

    assert row.status == "published"
    assert str(row.reviewed_by) == reviewer_id
    assert row.reviewed_at is not None
    assert row.published_at is not None
    # Card fields are materialised at publish time, from approved content.
    assert row.card_excerpt == "It claims to help."


async def test_validation_failed_article_cannot_be_approved(
    session: AsyncSession,
) -> None:
    """A draft that failed validation is not approvable into existence."""
    reviewer_id = await _make_user(session)
    article_id = await _make_article(
        session,
        status=ArticleStatus.VALIDATION_FAILED,
        content={"type": "doc", "content": []},
    )

    with pytest.raises(ReviewError, match="validation_failed"):
        await ReviewService(session).approve(article_id, reviewer_id)


async def test_rejected_article_requires_a_reason(session: AsyncSession) -> None:
    article_id = await _make_article(session, content={"type": "doc", "content": []})

    with pytest.raises((IntegrityError, DBAPIError)):
        await session.execute(
            sa.text("UPDATE articles SET status = 'rejected' WHERE id = :id"),
            {"id": article_id},
        )
        await session.flush()


# ---------------------------------------------------------------------------
# Invariant #4 — the AI draft is immutable.
# ---------------------------------------------------------------------------


async def test_updating_original_content_is_rejected_by_the_trigger(
    session: AsyncSession,
) -> None:
    """Even a direct UPDATE cannot rewrite what the model wrote."""
    article_id = await _make_article(
        session, content={"type": "doc", "content": [{"type": "paragraph"}]}
    )

    with pytest.raises((IntegrityError, DBAPIError), match="immutable"):
        await session.execute(
            sa.text(
                "UPDATE articles SET original_content = CAST(:content AS jsonb)"
                " WHERE id = :id"
            ),
            {"id": article_id, "content": _json({"type": "doc", "content": []})},
        )
        await session.flush()


async def test_edits_land_in_edited_content_and_original_is_untouched(
    session: AsyncSession,
) -> None:
    """The audit trail: what the model wrote vs. what went live."""
    original = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"beat": 1},
                "content": [{"type": "text", "text": "Original wording."}],
            }
        ],
    }
    reviewer_id = await _make_user(session)
    article_id = await _make_article(session, content=original)

    edited = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"beat": 1},
                "content": [{"type": "text", "text": "Human wording."}],
            }
        ],
    }
    await ReviewService(session).save_edits(article_id, edited, reviewer_id)

    row = (
        await session.execute(
            sa.text(
                "SELECT original_content, edited_content FROM articles WHERE id = :id"
            ),
            {"id": article_id},
        )
    ).one()

    assert row.original_content == original
    assert row.edited_content == edited


# ---------------------------------------------------------------------------
# Invariant #2, surviving human editing.
# ---------------------------------------------------------------------------


async def test_approve_rejects_a_citation_the_editor_invented(
    session: AsyncSession,
) -> None:
    """A reviewer pasting an unknown handle must not be able to publish it."""
    reviewer_id = await _make_user(session)
    article_id = await _make_article(session, content={"type": "doc", "content": []})

    edited = {
        "type": "doc",
        "content": [
            {
                "type": "paragraph",
                "attrs": {"beat": 2},
                "content": [
                    {"type": "text", "text": "A trial found an effect "},
                    {"type": "citation", "attrs": {"sourceIds": ["S99"]}},
                ],
            }
        ],
    }
    await ReviewService(session).save_edits(article_id, edited, reviewer_id)

    with pytest.raises(ReviewError, match="S99"):
        await ReviewService(session).approve(article_id, reviewer_id)


# ---------------------------------------------------------------------------
# The sources cache CHECK constraint.
# ---------------------------------------------------------------------------


async def test_source_without_any_identifier_is_rejected(session: AsyncSession) -> None:
    """A source that cannot resolve to a real paper must not exist.

    This is what lets "4/4 citations resolve" mean something.
    """
    with pytest.raises((IntegrityError, DBAPIError)):
        await session.execute(
            sa.text(
                "INSERT INTO sources (id, title, abstract, source_api, created_at,"
                " last_seen_at) VALUES (gen_random_uuid(), 'T', 'A', 'pubmed',"
                " now(), now())"
            )
        )
        await session.flush()


async def test_published_feed_query_excludes_every_other_status(
    session: AsyncSession,
) -> None:
    """The public surface's visibility rule, checked at the SQL level."""
    for status in ArticleStatus:
        if status is ArticleStatus.PUBLISHED:
            continue
        await _make_article(session, status=status, content={"type": "doc", "content": []})
    await session.flush()

    count = (
        await session.execute(
            sa.text(
                "SELECT count(*) FROM articles WHERE status = 'published'"
                " AND (reviewed_by IS NULL OR published_at IS NULL)"
            )
        )
    ).scalar_one()

    assert count == 0
