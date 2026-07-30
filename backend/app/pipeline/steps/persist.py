"""Stages 5 and 6 — validate the draft, then persist it.

Kept in one module because they are two halves of one decision: validation
determines the status the article is written with, and neither half is
meaningful without the other.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import ValidationFailure
from app.domain.enums import ArticleStatus
from app.domain.models import Article, ArticleSource
from app.evidence.validation import summarise_failures, validate_draft
from app.pipeline.stages import PipelineContext, StageError, StageName
from app.services.tiptap import MalformedBodyError, body_text_to_doc

logger = logging.getLogger(__name__)


class ValidateStage:
    """Stage 5 — enforce invariants #2 and #3 before anything is written."""

    name = StageName.VALIDATE

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Run ``validate_draft`` and attach the report to the context.

        **A failing report is not a stage error.** The stage succeeds; the
        article is simply written as ``validation_failed`` instead of
        ``pending_review``. Raising here would lose the report, and the report
        is the entire diagnostic value of catching the problem — "the model
        cited S9, which was never provided" is actionable; "stage 5 failed" is
        not.
        """
        if ctx.draft is None or ctx.synthesis_input is None:
            raise StageError(self.name, "synthesis stage did not run")

        report = await validate_draft(self._session, ctx.draft, ctx.synthesis_input)

        ctx.record_metrics(
            self.name,
            {
                "passed": report.passed,
                "citations_resolved": report.citations_resolved,
                "citations_total": report.citations_total,
                "best_evidence_grade": str(report.best_evidence_grade),
                "failure_codes": sorted({f.code for f in report.failures}),
                "summary": summarise_failures(report),
            },
        )

        return ctx.model_copy(update={"validation": report})


class PersistStage:
    """Stage 6 — write the article and its provenance.

    This is the pipeline's terminal write, and the only place it touches
    ``articles``. Note what it cannot do: there is no code path from here to
    ``published``. That status is reachable only through
    ``ReviewService.approve()`` with an authenticated reviewer, and the DB
    CHECK constraint refuses it without one. Invariant #1 is structural, not a
    convention this stage is politely observing.
    """

    name = StageName.PERSIST

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Write the article, its provenance rows, and the validation report."""
        if ctx.draft is None or ctx.validation is None or ctx.extraction is None:
            raise StageError(self.name, "earlier stages did not complete")

        report = ctx.validation

        # A body we cannot render is a draft problem, not a system problem —
        # record it as a validation failure rather than raising.
        try:
            content = body_text_to_doc(ctx.draft.body)
        except MalformedBodyError as exc:
            content = {"type": "doc", "content": []}
            report = report.model_copy(
                update={
                    "passed": False,
                    "failures": [
                        *report.failures,
                        ValidationFailure(code="malformed_body", message=str(exc)),
                    ],
                }
            )

        article_status = (
            ArticleStatus.PENDING_REVIEW if report.passed else ArticleStatus.VALIDATION_FAILED
        )

        article = Article(
            slug=_slugify(ctx.draft.headline),
            status=article_status,
            topic=ctx.topic,
            source_blurb=ctx.blurb,
            product=ctx.extraction.product,
            target_claims=ctx.extraction.target_claims,
            ingredients=ctx.extraction.ingredients,
            headline=ctx.draft.headline,
            summary=ctx.draft.summary,
            verdict=ctx.draft.verdict,
            verdict_qualifier=ctx.draft.verdict_qualifier,
            # Written once. A DB trigger rejects any later UPDATE that changes
            # it — human edits go to edited_content.
            original_content=content,
            edited_content=None,
            evidence_grade=report.best_evidence_grade,
            validation_report=report.model_dump(mode="json"),
            pipeline_run_id=ctx.run_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._session.add(article)
        await self._session.flush()

        cited = ctx.draft.all_cited_handles()
        seen: set[tuple[str, str]] = set()
        for claim, ranked in ctx.ranked.items():
            for entry in ranked:
                key = (entry.source_id, claim)
                if key in seen:
                    continue
                seen.add(key)
                self._session.add(
                    ArticleSource(
                        article_id=article.id,
                        source_id=entry.source_id,
                        claim=claim,
                        citation_handle=entry.citation_handle,
                        # Uncited sources are recorded deliberately: the review
                        # panel shows what was retrieved and not used, because
                        # omission is the failure mode human review exists to
                        # catch.
                        was_cited=entry.citation_handle in cited,
                        relevance_score=entry.final_score,
                    )
                )

        await self._session.flush()

        logger.info(
            "persisted article %s as %s (%s)",
            article.id,
            article_status,
            report.badge,
        )

        ctx.record_metrics(
            self.name,
            {
                "article_id": article.id,
                "status": str(article_status),
                "source_links": len(seen),
                "cited_sources": len(cited),
            },
        )

        return ctx.model_copy(update={"article_id": article.id, "validation": report})


def _slugify(headline: str) -> str:
    """Kebab-case headline plus a short random suffix.

    The suffix guarantees uniqueness without a retry loop — two articles on the
    same topic will produce near-identical headlines, and a unique-index
    violation at the end of a multi-minute pipeline run is an expensive way to
    discover that.
    """
    base = re.sub(r"[^a-z0-9]+", "-", headline.lower()).strip("-")[:60].strip("-")
    return f"{base or 'article'}-{secrets.token_hex(3)}"
