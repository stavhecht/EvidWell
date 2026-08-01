"""The human-in-the-loop service. Invariant #1 lives here.

``approve()`` is the **only** function in the system that sets
``status = 'published'``. Nothing in the pipeline can reach it; there is no
admin endpoint that bypasses it; the DB CHECK constraint refuses a published
row without ``reviewed_by`` and ``reviewed_at``. If you are reviewing this
design for the human-in-the-loop guarantee, this file and that constraint are
the two things to check.
"""

from __future__ import annotations

import base64
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ArticleStatus, StudyType
from app.domain.models import Article, ArticleSource, Source
from app.evidence.grading import is_weak_evidence
from app.services.card import derive_card
from app.services.tiptap import cited_handles_in_doc

logger = logging.getLogger(__name__)


class ReviewError(RuntimeError):
    """An illegal review transition was attempted. Surfaces as HTTP 409."""


class ReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def save_edits(self, article_id: str, content: dict, reviewer_id: str) -> None:
        """Autosave the editor's content into ``edited_content``.

        Writes ``edited_content`` only. ``original_content`` is never touched —
        the DB trigger will reject the statement if a bug tries, so the audit
        trail of "what the model wrote vs. what went live" holds even against
        application error.

        Called on a debounce from the editor, so it must be cheap and
        idempotent. **No validation runs here** — failing a reviewer's autosave
        mid-sentence, while a citation is momentarily orphaned, would be
        hostile. Validation happens at approve time, where it can block
        publication rather than typing.

        Raises:
            ReviewError: the article is not in ``pending_review``.
        """
        article = await self._get(article_id)
        if article.status != ArticleStatus.PENDING_REVIEW:
            raise ReviewError(
                f"cannot edit an article in state '{article.status}'; "
                "only drafts pending review are editable"
            )

        article.edited_content = content
        await self._session.flush()
        logger.debug("autosaved edits to article %s by reviewer %s", article_id, reviewer_id)

    async def approve(self, article_id: str, reviewer_id: str) -> None:
        """Publish an article. The only path to ``published``.

        Re-validates the *edited* content's citations before publishing. A
        reviewer can orphan a citation by deleting a sentence, or paste a handle
        that was never provided — invariant #2 has to survive human editing, not
        just generation.

        Raises:
            ReviewError: wrong status, or edited content that no longer
                validates. The message names the specific handles, because a
                reviewer needs to know *which* citation broke.
        """
        article = await self._get(article_id)

        if article.status != ArticleStatus.PENDING_REVIEW:
            # Deliberately includes validation_failed: a draft that failed
            # validation cannot be approved into existence, it has to be
            # regenerated.
            raise ReviewError(
                f"cannot publish an article in state '{article.status}'; "
                "only drafts pending review can be published"
            )

        content = article.edited_content or article.original_content
        await self._assert_citations_still_resolve(article_id, content)

        card = derive_card(article.headline, content, article.verdict)
        now = datetime.now(UTC)

        article.card_headline = card.headline
        article.card_excerpt = card.excerpt
        article.card_verdict = card.verdict
        article.status = ArticleStatus.PUBLISHED
        article.reviewed_by = reviewer_id
        article.reviewed_at = now
        article.published_at = now

        await self._session.flush()
        logger.info("article %s published by reviewer %s", article_id, reviewer_id)

    #: States a draft can be rejected from.
    #:
    #: ``validation_failed`` is included because rejection is the only way a
    #: draft leaves the queue — there is no delete — and without it those
    #: drafts have no available action at all and accumulate in their tab
    #: forever. They are still never approvable; this is the discard path, not
    #: a route around invariant #2.
    _REJECTABLE = frozenset(
        {ArticleStatus.PENDING_REVIEW, ArticleStatus.VALIDATION_FAILED}
    )

    async def reject(self, article_id: str, reviewer_id: str, reason: str) -> None:
        """Reject a draft. Terminal, with a required reason.

        There is no delete: rejection is a recorded state. The reasons drafts
        get rejected are the highest-value signal available for improving the
        synthesis prompt, and deleting them throws that away. That argument
        applies with most force to ``validation_failed`` drafts, which is why
        discarding one records a reason rather than removing the row.
        """
        article = await self._get(article_id)
        if article.status not in self._REJECTABLE:
            raise ReviewError(
                f"cannot reject an article in state '{article.status}'"
            )

        article.status = ArticleStatus.REJECTED
        article.rejection_reason = reason
        article.reviewed_by = reviewer_id
        article.reviewed_at = datetime.now(UTC)
        await self._session.flush()
        logger.info("article %s rejected by reviewer %s", article_id, reviewer_id)

    async def _assert_citations_still_resolve(self, article_id: str, content: dict) -> None:
        """Every handle in the edited body must still map to a linked source."""
        emitted = cited_handles_in_doc(content)
        if not emitted:
            return

        result = await self._session.execute(
            select(ArticleSource.citation_handle).where(
                ArticleSource.article_id == article_id
            )
        )
        known = {row[0] for row in result.all()}

        unknown = sorted(emitted - known)
        if unknown:
            raise ReviewError(
                f"cannot publish: {', '.join(unknown)} "
                f"{'is' if len(unknown) == 1 else 'are'} not among this article's "
                "sources. Remove the citation or restore the original text."
            )

    async def queue(
        self,
        status: ArticleStatus = ArticleStatus.PENDING_REVIEW,
        cursor: str | None = None,
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """The review queue.

        Pending review is oldest-first, deliberately: newest-first lets a
        slow-moving queue strand old drafts behind fresher ones forever.
        Terminal states are newest-first, because there the interesting rows are
        the recent ones.

        Also serves the ``validation_failed`` tab — those drafts are never
        approvable, but they are the system's prompt-bug feedback and must be
        visible.
        """
        oldest_first = status == ArticleStatus.PENDING_REVIEW
        order = Article.created_at.asc() if oldest_first else Article.created_at.desc()

        statement = select(Article).where(Article.status == status).order_by(order, Article.id)

        if cursor:
            created_at, article_id = _decode_cursor(cursor)
            keyset = tuple_(Article.created_at, Article.id)
            statement = statement.where(
                keyset > (created_at, article_id)
                if oldest_first
                else keyset < (created_at, article_id)
            )

        result = await self._session.execute(statement.limit(limit + 1))
        articles = list(result.scalars())

        next_cursor = None
        if len(articles) > limit:
            articles = articles[:limit]
            last = articles[-1]
            next_cursor = _encode_cursor(last.created_at, last.id)

        items = [
            {
                "id": article.id,
                "topic": article.topic,
                "headline": article.headline,
                "verdict": article.verdict,
                "status": article.status,
                "evidence_grade": article.evidence_grade,
                "validation_badge": _badge(article.validation_report),
                "has_weak_evidence": is_weak_evidence(StudyType(article.evidence_grade)),
                "created_at": article.created_at,
            }
            for article in articles
        ]
        return items, next_cursor

    async def article_detail(self, article_id: str) -> dict[str, Any]:
        """Draft + sources + validation report for the review screen.

        One query joining ``article_sources`` → ``sources``, ordered by claim
        then handle, so the panel renders without client-side joining. This is
        the "fast source access" requirement: the reviewer's time is the scarce
        resource, and a waterfall of requests spends it.

        Returns cited **and** uncited sources. Uncited ones are what the model
        chose to leave out, and omission is the failure mode human review exists
        to catch.
        """
        article = await self._get(article_id)

        result = await self._session.execute(
            select(ArticleSource, Source)
            .join(Source, Source.id == ArticleSource.source_id)
            .where(ArticleSource.article_id == article_id)
            .order_by(ArticleSource.claim, ArticleSource.citation_handle)
        )

        sources = [
            {
                "source_id": source.id,
                "citation_handle": link.citation_handle,
                "claim": link.claim,
                "title": source.title,
                "journal": source.journal,
                "year": source.year,
                "study_type": source.study_type,
                "citation_count": source.citation_count,
                "url": source.resolved_url,
                "pmid": source.pmid,
                "doi": source.doi,
                "was_cited": link.was_cited,
                "relevance_score": link.relevance_score,
                "is_weak_evidence": is_weak_evidence(StudyType(source.study_type)),
            }
            for link, source in result.all()
        ]

        return {
            "id": article.id,
            "status": article.status,
            "topic": article.topic,
            "product": article.product,
            "target_claims": article.target_claims,
            "ingredients": article.ingredients,
            "headline": article.headline,
            "summary": article.summary,
            "verdict": article.verdict,
            "verdict_qualifier": article.verdict_qualifier,
            "evidence_grade": article.evidence_grade,
            "original_content": article.original_content,
            "edited_content": article.edited_content,
            "validation_report": article.validation_report,
            "sources": sources,
            "pipeline_run_id": article.pipeline_run_id,
            "created_at": article.created_at,
        }

    async def _get(self, article_id: str) -> Article:
        article = await self._session.get(Article, article_id)
        if article is None:
            raise ReviewError(f"article {article_id} not found")
        return article


def _badge(report: dict[str, Any] | None) -> str:
    if not report:
        return "not validated"
    resolved = report.get("citations_resolved", 0)
    total = report.get("citations_total", 0)
    return f"{resolved}/{total} citations resolve"


def _encode_cursor(created_at: datetime, article_id: str) -> str:
    """Opaque keyset cursor.

    Keyset rather than offset: the queue mutates while a reviewer works through
    it, and an offset would skip or repeat drafts as rows leave the filtered
    set. The id is the tiebreaker for rows sharing a timestamp.
    """
    raw = f"{created_at.isoformat()}|{article_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        timestamp, article_id = raw.split("|", 1)
        return datetime.fromisoformat(timestamp), article_id
    except (ValueError, UnicodeDecodeError) as exc:
        raise ReviewError("malformed cursor") from exc
