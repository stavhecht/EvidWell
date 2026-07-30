"""The local pipeline worker.

A separate process that polls ``pipeline_runs`` for queued work and drives the
orchestrator. Separate from the API process on purpose: a generation run takes
minutes, and a request-scoped background task ties that lifetime to a web
worker that may be recycled mid-run.

Production path (DESIGN.md §10): this file goes away. EventBridge triggers a
Step Functions execution, each stage becomes a Lambda, and the polling loop
becomes the state machine. The orchestrator and the stages are unchanged —
only this dispatch shell is local-only.

Run with:  python -m app.pipeline.runner
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.config import get_settings
from app.db import dispose_engine, get_session_factory
from app.domain.enums import RunStatus
from app.domain.models import PipelineRun
from app.pipeline.orchestrator import PipelineOrchestrator, build_default_pipeline

logger = logging.getLogger(__name__)

#: A run still marked `running` but older than this had its worker killed.
#: Left alone it is a lie the console faithfully displays forever.
STALE_RUN_AFTER = timedelta(minutes=30)


class PipelineWorker:
    def __init__(self, poll_interval: float = 5.0) -> None:
        self._poll_interval = poll_interval
        self._shutdown = asyncio.Event()

    def request_shutdown(self) -> None:
        """Signal handler target. The in-flight run is allowed to finish."""
        logger.info("shutdown requested; finishing in-flight run")
        self._shutdown.set()

    async def run_forever(self) -> None:
        """Poll for queued runs and execute them until shut down."""
        settings = get_settings()
        await self._reset_stale_runs()

        logger.info("worker started (poll interval %.1fs)", self._poll_interval)
        while not self._shutdown.is_set():
            run = await self._claim_next_run()
            if run is None:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._shutdown.wait(), timeout=self._poll_interval
                    )
                continue

            run_id, topic, blurb = run
            logger.info("claimed run %s (topic=%r)", run_id, topic)

            factory = get_session_factory()
            try:
                async with factory() as session:
                    stages = build_default_pipeline(session, settings)
                    orchestrator = PipelineOrchestrator(session, stages, factory)
                    await orchestrator.run(run_id, topic, blurb)
                    await session.commit()
            except Exception:
                # The orchestrator records stage-level failures itself; this
                # catches anything that escapes it (a broken pipeline build, a
                # dead database) so one bad run cannot kill the worker.
                logger.exception("run %s crashed outside the orchestrator", run_id)
                await self._mark_failed(run_id, "worker crashed outside the orchestrator")

        logger.info("worker stopped")

    async def _claim_next_run(self) -> tuple[str, str, str | None] | None:
        """Atomically claim one queued run.

        ``FOR UPDATE SKIP LOCKED`` is correct from the start even at
        concurrency 1: it costs nothing, and it means raising concurrency later
        is a config change rather than a correctness change.
        """
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                select(PipelineRun)
                .where(PipelineRun.status == RunStatus.QUEUED)
                .order_by(PipelineRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            run = result.scalar_one_or_none()
            if run is None:
                await session.commit()
                return None

            run.status = RunStatus.RUNNING
            run.started_at = datetime.now(UTC)
            claimed = (run.id, run.topic, run.source_blurb)
            await session.commit()
            return claimed

    async def _reset_stale_runs(self) -> None:
        """Requeue runs abandoned by a killed worker.

        Without this, a hard kill leaves rows stuck in ``running`` forever and
        the console reports work that no process is doing.
        """
        cutoff = datetime.now(UTC) - STALE_RUN_AFTER
        factory = get_session_factory()
        async with factory() as session:
            result = await session.execute(
                update(PipelineRun)
                .where(
                    PipelineRun.status == RunStatus.RUNNING,
                    PipelineRun.started_at < cutoff,
                )
                .values(status=RunStatus.QUEUED, started_at=None)
            )
            await session.commit()
            requeued = getattr(result, "rowcount", 0)
            if requeued:
                logger.warning("requeued %d stale run(s)", requeued)

    async def _mark_failed(self, run_id: str, message: str) -> None:
        factory = get_session_factory()
        async with factory() as session:
            await session.execute(
                update(PipelineRun)
                .where(PipelineRun.id == run_id)
                .values(
                    status=RunStatus.FAILED,
                    error={"message": message},
                    finished_at=datetime.now(UTC),
                )
            )
            await session.commit()


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    settings = get_settings()
    worker = PipelineWorker(poll_interval=settings.worker_poll_interval_seconds)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.request_shutdown)

    try:
        await worker.run_forever()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
