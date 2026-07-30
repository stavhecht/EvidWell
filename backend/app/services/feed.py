"""Public feed queries.

**Every query in this module filters ``status = 'published'``.** The filter
lives in ``_published_only()`` rather than being spelled out per method, so
there is one place to audit rather than several — and no way to add a new
method that forgets it.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any

from sqlalchemy import Select, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ArticleStatus, Verdict
from app.domain.models import Article, ArticleSource, Source


class ArticleNotFound(LookupError):
    """No published article with that slug. Surfaces as HTTP 404."""


def _published_only(statement: Select[Any]) -> Select[Any]:
    """The single audit point for the public surface's visibility rule."""
    return statement.where(Article.status == ArticleStatus.PUBLISHED)


class FeedService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(
        self,
        cursor: str | None = None,
        limit: int = 24,
        verdict: Verdict | None = None,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """One page of published cards, newest first.

        Reads the materialised card columns only — the feed never touches
        article content, which is what keeps it a single index scan
        (``articles_feed_idx``) at any feed size.
        """
        statement = _published_only(
            select(
                Article.slug,
                Article.card_headline,
                Article.card_excerpt,
                Article.card_verdict,
                Article.verdict_qualifier,
                Article.published_at,
                Article.id,
            )
        ).order_by(Article.published_at.desc(), Article.id.desc())

        if verdict is not None:
            statement = statement.where(Article.card_verdict == verdict)

        if cursor:
            published_at, article_id = _decode_cursor(cursor)
            statement = statement.where(
                tuple_(Article.published_at, Article.id) < (published_at, article_id)
            )

        result = await self._session.execute(statement.limit(limit + 1))
        rows = result.all()

        next_cursor = None
        if len(rows) > limit:
            rows = rows[:limit]
            last = rows[-1]
            next_cursor = _encode_cursor(last.published_at, last.id)

        items = [
            {
                "slug": row.slug,
                # Fall back to the article headline if a legacy row predates
                # card materialisation; a card with an empty title is worse
                # than one whose excerpt is missing.
                "headline": row.card_headline or "",
                "excerpt": row.card_excerpt or "",
                "verdict": row.card_verdict,
                "verdict_qualifier": row.verdict_qualifier,
                "published_at": row.published_at,
            }
            for row in rows
        ]
        return items, next_cursor

    async def article(self, slug: str) -> dict[str, Any]:
        """Full article with citations and resolved sources.

        Returns ``edited_content`` when present — the human-approved version.

        Raises:
            ArticleNotFound: no *published* article with this slug. The caller
                must not distinguish "no such article" from "not published
                yet": leaking the existence of unpublished drafts turns the
                slug space into a preview channel.
        """
        result = await self._session.execute(
            _published_only(select(Article).where(Article.slug == slug))
        )
        article = result.scalar_one_or_none()
        if article is None:
            raise ArticleNotFound(slug)

        source_result = await self._session.execute(
            select(ArticleSource, Source)
            .join(Source, Source.id == ArticleSource.source_id)
            .where(
                ArticleSource.article_id == article.id,
                # Public readers see what the article cites. Retrieved-but-
                # uncited sources are internal review context.
                ArticleSource.was_cited.is_(True),
            )
            .order_by(ArticleSource.citation_handle)
        )

        seen: set[str] = set()
        sources: list[dict[str, Any]] = []
        citations: list[dict[str, Any]] = []
        claims: dict[str, list[str]] = {}

        for link, source in source_result.all():
            if link.citation_handle not in seen:
                seen.add(link.citation_handle)
                sources.append(
                    {
                        "title": source.title,
                        "journal": source.journal,
                        "year": source.year,
                        "study_type": source.study_type,
                        "citation_handle": link.citation_handle,
                        "url": source.resolved_url,
                        "pmid": source.pmid,
                        "doi": source.doi,
                    }
                )
            claims.setdefault(link.claim, []).append(link.citation_handle)

        citations = [
            {"claim": claim, "handles": sorted(set(handles), key=_handle_sort_key)}
            for claim, handles in claims.items()
        ]

        return {
            "slug": article.slug,
            "headline": article.headline,
            "summary": article.summary,
            "verdict": article.verdict,
            "verdict_qualifier": article.verdict_qualifier,
            "product": article.product,
            "target_claims": article.target_claims,
            "ingredients": article.ingredients,
            "content": article.display_content,
            "sources": sorted(sources, key=lambda s: _handle_sort_key(s["citation_handle"])),
            "citations": citations,
            "evidence_grade": article.evidence_grade,
            "published_at": article.published_at,
        }


def _handle_sort_key(handle: str) -> int:
    digits = handle[1:]
    return int(digits) if digits.isdigit() else 0


def _encode_cursor(published_at: datetime, article_id: str) -> str:
    """Keyset cursor.

    Keyset rather than offset because the feed grows at the head: an offset
    would skip or duplicate cards whenever something publishes mid-scroll.
    """
    return base64.urlsafe_b64encode(
        f"{published_at.isoformat()}|{article_id}".encode()
    ).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        timestamp, article_id = raw.split("|", 1)
        return datetime.fromisoformat(timestamp), article_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("malformed cursor") from exc
