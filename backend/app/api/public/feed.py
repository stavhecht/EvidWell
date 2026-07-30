"""Public feed endpoints. Read-only, unauthenticated.

The ``status = 'published'`` filter is not applied here — it lives in
``services/feed.py::_published_only`` so there is one place to audit and no way
for a new endpoint to forget it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text

from app.api.deps import SessionDep
from app.api.public.schemas import ArticleOut, FeedPageOut
from app.domain.enums import Verdict
from app.services.feed import ArticleNotFound, FeedService

router = APIRouter(tags=["public"])


@router.get("/feed", response_model=FeedPageOut)
async def get_feed(
    session: SessionDep,
    cursor: Annotated[str | None, Query(description="Opaque cursor from next_cursor")] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 24,
    verdict: Annotated[Verdict | None, Query(description="Filter by verdict")] = None,
) -> FeedPageOut:
    """Cursor-paginated feed of published article cards.

    ``limit`` defaults to 24 so the masonry layout can fill several columns on
    a wide viewport without a second round trip on first paint.
    """
    try:
        items, next_cursor = await FeedService(session).page(cursor, limit, verdict)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed cursor"
        ) from None
    return FeedPageOut(items=items, next_cursor=next_cursor)


@router.get("/articles/{slug}", response_model=ArticleOut)
async def get_article(slug: str, session: SessionDep) -> ArticleOut:
    """Full article with citations and resolved sources.

    404 for any slug that is not published, including one that exists in
    ``pending_review`` — the response must not distinguish "no such article"
    from "not published yet".
    """
    try:
        article = await FeedService(session).article(slug)
    except ArticleNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Article not found"
        ) from None
    return ArticleOut(**article)


@router.get("/healthz")
async def healthz(session: SessionDep) -> dict[str, str]:
    """Liveness plus database reachability.

    A health check that only proves the process is up will report green while
    every request 500s on a dead connection pool.
    """
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        ) from None
    return {"status": "ok"}
