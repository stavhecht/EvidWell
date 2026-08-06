"""Runs the stages in order and records what happened.

The recording is the reason this exists rather than a bare `for` loop. Without
``pipeline_runs``, a draft that failed citation validation is indistinguishable
from a pipeline that never ran — invariant #2 would be enforced but invisible,
which in practice means unmaintained.

Locally this drives function calls. In production the same sequence becomes a
Step Functions state machine; because stages exchange a serialisable context,
that swap does not touch this file's logic, only how ``stage.run`` is dispatched.
"""

from __future__ import annotations

import logging
import traceback
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.enums import RunStatus
from app.domain.models import PipelineRun, PipelineStageRun
from app.llm.embeddings.factory import build_embedding_provider
from app.llm.factory import build_generative_clients
from app.pipeline.stages import PipelineContext, Stage, StageError, StageName
from app.pipeline.steps.extract import ExtractStage
from app.pipeline.steps.persist import PersistStage, ValidateStage
from app.pipeline.steps.rank import RankStage
from app.pipeline.steps.retrieve import RetrieveStage
from app.pipeline.steps.synthesize import SynthesizeStage
from app.retrieval.cache import SourceCache
from app.retrieval.factory import build_http_client, build_providers
from app.retrieval.query_builder import TemplateQueryStrategy
from app.retrieval.rerank import RerankConfig, SemanticReranker

logger = logging.getLogger(__name__)


class PipelineOrchestrator:
    """Drives the stages and writes the run record.

    ``bookkeeping_factory`` is a *separate* session factory on purpose. Stage
    bookkeeping must commit independently of the pipeline transaction: if the
    article write rolls back, the record of what happened has to survive, or
    the failures you most need to debug are exactly the ones that erase their
    own evidence.
    """

    def __init__(
        self,
        session: AsyncSession,
        stages: list[Stage],
        bookkeeping_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session = session
        self._stages = stages
        self._bookkeeping = bookkeeping_factory

    async def run(self, run_id: str, topic: str, blurb: str | None = None) -> PipelineContext:
        """Execute every stage in order, recording each.

        Returns the final context whether the run succeeded or failed; callers
        inspect ``ctx.article_id`` and ``ctx.validation``.

        On failure, later stages stay ``queued`` so the console shows exactly
        where it stopped.
        """
        ctx = PipelineContext(run_id=run_id, topic=topic, blurb=blurb)
        await self._set_run_status(run_id, RunStatus.RUNNING, started=True)

        for ordinal, stage in enumerate(self._stages):
            stage_run_id = await self._record_stage_start(run_id, stage.name, ordinal)
            try:
                ctx = await stage.run(ctx)
            except StageError as exc:
                await self._record_stage_end(
                    stage_run_id,
                    failed=True,
                    error={
                        "type": "StageError",
                        "stage": str(stage.name),
                        "message": str(exc),
                        "retryable": exc.retryable,
                    },
                )
                await self._fail_run(run_id, stage.name, str(exc))
                return ctx
            except Exception as exc:
                await self._record_stage_end(
                    stage_run_id,
                    failed=True,
                    error={
                        "type": type(exc).__name__,
                        "stage": str(stage.name),
                        "message": str(exc),
                        "traceback": traceback.format_exc(limit=8),
                    },
                )
                await self._fail_run(run_id, stage.name, f"{type(exc).__name__}: {exc}")
                return ctx

            await self._record_stage_end(
                stage_run_id, metrics=ctx.metrics.get(str(stage.name))
            )

        await self._finish_run(run_id, ctx)
        return ctx

    async def _record_stage_start(
        self, run_id: str, stage: StageName, ordinal: int
    ) -> str:
        async with self._bookkeeping() as session:
            row = PipelineStageRun(
                run_id=run_id,
                stage=str(stage),
                ordinal=ordinal,
                status=RunStatus.RUNNING,
                started_at=datetime.now(UTC),
            )
            session.add(row)
            await session.commit()
            return row.id

    async def _record_stage_end(
        self,
        stage_run_id: str,
        *,
        failed: bool = False,
        error: dict[str, object] | None = None,
        metrics: dict[str, object] | None = None,
    ) -> None:
        async with self._bookkeeping() as session:
            await session.execute(
                update(PipelineStageRun)
                .where(PipelineStageRun.id == stage_run_id)
                .values(
                    status=RunStatus.FAILED if failed else RunStatus.SUCCEEDED,
                    error=error,
                    metrics=metrics,
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def _set_run_status(
        self, run_id: str, status: RunStatus, *, started: bool = False
    ) -> None:
        async with self._bookkeeping() as session:
            values: dict[str, object] = {"status": status}
            if started:
                values["started_at"] = datetime.now(UTC)
            await session.execute(
                update(PipelineRun).where(PipelineRun.id == run_id).values(**values)
            )
            await session.commit()

    async def _fail_run(self, run_id: str, stage: StageName, message: str) -> None:
        logger.error("run %s failed at stage %s: %s", run_id, stage, message)
        async with self._bookkeeping() as session:
            await session.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run_id)
                .values(
                    status=RunStatus.FAILED,
                    error={"stage": str(stage), "message": message},
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()

    async def _finish_run(self, run_id: str, ctx: PipelineContext) -> None:
        # This one write does NOT use the bookkeeping session, unlike every
        # other write in this class. ``article_id`` is a foreign key into
        # ``articles``, and the article was written by PersistStage into the
        # *pipeline* session, which the caller commits only after this method
        # returns. A bookkeeping write commits immediately, so it would point at
        # a row no other transaction can see yet — a ForeignKeyViolationError
        # that aborts the run and takes the uncommitted article down with it.
        #
        # Using the pipeline session makes "succeeded, article=X" atomic with X
        # itself, which is also the honest semantics: if the article fails to
        # commit, the run did not succeed. The failure paths stay on the
        # bookkeeping session — they carry no such FK, and surviving a
        # rolled-back pipeline transaction is exactly their purpose (§3.3).
        await self._session.execute(
            update(PipelineRun)
            .where(PipelineRun.id == run_id)
            .values(
                status=RunStatus.SUCCEEDED,
                article_id=ctx.article_id,
                input_tokens=ctx.usage.input_tokens,
                output_tokens=ctx.usage.output_tokens,
                finished_at=datetime.now(UTC),
            )
        )

        validation = ctx.validation
        logger.info(
            "run %s finished: article=%s validation=%s",
            run_id,
            ctx.article_id,
            "passed" if validation and validation.passed else "FAILED",
        )


def build_default_pipeline(session: AsyncSession, settings: Settings) -> list[Stage]:
    """Assemble the six stages with their dependencies.

    The single place where concrete clients (Ollama or Claude, an embedding
    provider, PubMed) are bound to the Protocols the stages depend on — so
    tests substitute fakes by calling a different builder, not by patching
    imports. Which generative and embedding provider gets bound is config; see
    ``llm/factory.py`` and ``llm/embeddings/factory.py``.
    """
    embedder = build_embedding_provider(settings)
    http = build_http_client(settings)
    providers = build_providers(settings, http)

    extraction_client, synthesis_client = build_generative_clients(settings)
    cache = SourceCache(session, embedder)
    reranker = SemanticReranker(session, embedder)

    rerank_config = RerankConfig(
        top_k=settings.retrieval_top_k,
        min_year=settings.retrieval_min_year,
    )

    return [
        ExtractStage(extraction_client),
        RetrieveStage(
            providers,
            TemplateQueryStrategy(min_year=settings.retrieval_min_year),
            cache,
            settings.retrieval_max_candidates_per_claim,
        ),
        RankStage(reranker, cache, rerank_config),
        SynthesizeStage(synthesis_client),
        ValidateStage(session),
        PersistStage(session),
    ]
