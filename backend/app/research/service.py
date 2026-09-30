"""Database reads and writes for research runs.

``start_research_run`` is the **one** way a research run is created: the desk's
button and n8n's weekly call both come through it, so a weekly run and a manual
one differ only in ``mode`` and ``requested_by``.

Bookkeeping writes (checkpoint, heartbeat, finish) take a session *factory* and
commit on their own, like the orchestrator's bookkeeping: a run's record of how
far it got has to survive whatever happens to the run.
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.domain.enums import (
    DiscoveryCandidateStatus,
    ResearchCandidateStatus,
    ResearchRunMode,
    ResearchRunStatus,
    RunStatus,
)
from app.domain.models import (
    DiscoveryCandidate,
    PipelineRun,
    ResearchCandidate,
    ResearchRun,
)
from app.research.contracts import (
    CandidateTopic,
    PastTopic,
    ResearchConfig,
    ResearchParams,
    ResearchStage,
    ResearchState,
)

logger = logging.getLogger(__name__)

_ACTIVE = (ResearchRunStatus.QUEUED, ResearchRunStatus.RUNNING)


class ResearchAlreadyActive(RuntimeError):
    """A research run is already queued or running."""


def make_label(now: datetime) -> str:
    return f"research_{now:%Y_%m_%d}_{secrets.token_hex(2)}"


async def start_research_run(
    session: AsyncSession,
    *,
    mode: ResearchRunMode,
    params: ResearchParams,
    requested_by: str | None,
    settings: Settings,
) -> ResearchRun:
    """Queue a research run. Does not commit — the request session does.

    Single-flight: a second trigger while one is queued or running is refused
    rather than queued behind it. Two runs a minute apart would spend the same
    Trends, news and PubMed budget to compute the same answer — and News API's
    free plan has 100 requests a day, which one run can use half of.

    Raises:
        ResearchAlreadyActive: naming the run that is in the way.
    """
    active = await session.scalar(
        select(ResearchRun.label).where(ResearchRun.status.in_(_ACTIVE)).limit(1)
    )
    if active is not None:
        raise ResearchAlreadyActive(f"research run {active} is already queued or running")

    now = datetime.now(UTC)
    config = ResearchConfig.resolve(settings, params)
    run = ResearchRun(
        label=make_label(now),
        mode=mode,
        status=ResearchRunStatus.QUEUED,
        stage=ResearchStage.QUEUED.value,
        params=params.model_dump(mode="json", exclude_none=True),
        config=config.model_dump(mode="json"),
        provider_status={},
        stage_log=[],
        notes=[],
        requested_by=requested_by,
        attempts=0,
        created_at=now,
    )
    session.add(run)
    await session.flush()
    logger.info("queued research run %s (%s)", run.label, mode.value)
    return run


# --- worker side -----------------------------------------------------------


async def claim_next(factory: async_sessionmaker[AsyncSession]) -> tuple[str, str] | None:
    """Claim the oldest queued research run: ``(id, label)``, or None."""
    now = datetime.now(UTC)
    async with factory() as session:
        run = (
            await session.execute(
                select(ResearchRun)
                .where(ResearchRun.status == ResearchRunStatus.QUEUED)
                .order_by(ResearchRun.created_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if run is None:
            await session.commit()
            return None
        run.status = ResearchRunStatus.RUNNING
        run.started_at = now
        run.heartbeat_at = now
        run.attempts += 1
        claimed = (run.id, run.label)
        await session.commit()
        return claimed


async def beat(factory: async_sessionmaker[AsyncSession], run_id: str) -> None:
    async with factory() as session:
        await session.execute(
            update(ResearchRun)
            .where(ResearchRun.id == run_id)
            .values(heartbeat_at=datetime.now(UTC))
        )
        await session.commit()


async def recover_stale(
    factory: async_sessionmaker[AsyncSession], stale_after: timedelta
) -> int:
    """Fail research runs whose worker stopped reporting. Never requeued.

    Unlike a pipeline run, a research run is cheap to start again and has no
    article to protect: failing it and letting the next trigger start fresh is
    simpler than resuming a half-measured candidate pool.
    """
    cutoff = datetime.now(UTC) - stale_after
    async with factory() as session:
        result = await session.execute(
            update(ResearchRun)
            .where(
                ResearchRun.status == ResearchRunStatus.RUNNING,
                func.coalesce(ResearchRun.heartbeat_at, ResearchRun.started_at) < cutoff,
            )
            .values(
                status=ResearchRunStatus.FAILED,
                error={"message": "worker stopped reporting; start a new research run"},
                finished_at=datetime.now(UTC),
            )
            .returning(ResearchRun.label)
        )
        labels = result.scalars().all()
        await session.commit()
    for label in labels:
        logger.error("research run %s abandoned by its worker; marked failed", label)
    return len(labels)


async def load_state(
    factory: async_sessionmaker[AsyncSession], run_id: str, *, today: Any
) -> ResearchState:
    async with factory() as session:
        run = await session.get(ResearchRun, run_id)
        if run is None:
            raise LookupError(f"research run {run_id} not found")
        return ResearchState(
            run_id=run.id,
            label=run.label,
            mode=run.mode,
            config=ResearchConfig.model_validate(run.config),
            today=today,
        )


async def checkpoint(factory: async_sessionmaker[AsyncSession], state: ResearchState) -> None:
    """Write the run's progress and every candidate so far.

    After every node, so the desk can show a run while it is still going, and a
    run that dies keeps what it had measured.
    """
    async with factory() as session:
        await session.execute(
            update(ResearchRun)
            .where(ResearchRun.id == state.run_id)
            .values(
                stage=state.stage.value,
                provider_status={
                    name: status.model_dump(mode="json")
                    for name, status in state.provider_status.items()
                },
                stage_log=state.stage_log,
                notes=state.notes,
                heartbeat_at=datetime.now(UTC),
            )
        )
        rows = [candidate_row(candidate, state.run_id) for candidate in state.candidates]
        if rows:
            statement = insert(ResearchCandidate).values(rows)
            updatable = {
                key: statement.excluded[key]
                for key in rows[0]
                if key not in ("id", "research_run_id")
            }
            await session.execute(
                statement.on_conflict_do_update(index_elements=["id"], set_=updatable)
            )
        await session.commit()


async def finish(factory: async_sessionmaker[AsyncSession], state: ResearchState) -> None:
    await checkpoint(factory, state)
    failed = state.failed is not None
    async with factory() as session:
        await session.execute(
            update(ResearchRun)
            .where(ResearchRun.id == state.run_id)
            .values(
                status=ResearchRunStatus.FAILED if failed else ResearchRunStatus.COMPLETED,
                stage=state.stage.value,
                error={"message": state.failed} if failed else None,
                finished_at=datetime.now(UTC),
            )
        )
        await session.commit()


async def prune_undecided(
    factory: async_sessionmaker[AsyncSession], retention: timedelta
) -> int:
    """Delete candidates nobody decided on, from runs finished before ``retention``.

    Promoted and dismissed candidates stay — novelty reads them, and a promoted
    one is the provenance of a pipeline run. The run rows stay too (a few KB
    each): they are the record that a run happened and what it saw.
    """
    cutoff = datetime.now(UTC) - retention
    async with factory() as session:
        result = await session.execute(
            delete(ResearchCandidate).where(
                ResearchCandidate.status.not_in(
                    (ResearchCandidateStatus.PROMOTED, ResearchCandidateStatus.DISMISSED)
                ),
                ResearchCandidate.research_run_id.in_(
                    select(ResearchRun.id).where(ResearchRun.finished_at < cutoff)
                ),
            )
        )
        await session.commit()
    return int(getattr(result, "rowcount", 0) or 0)


def candidate_row(candidate: CandidateTopic, run_id: str) -> dict[str, Any]:
    return {
        "id": candidate.topic_id,
        "research_run_id": run_id,
        "canonical_topic": candidate.canonical_topic,
        "category": candidate.category.value,
        "queries": candidate.queries,
        "signals": candidate.signals_json(),
        "scores": candidate.scores.model_dump(mode="json"),
        "evidence_status": candidate.evidence_status.value
        if candidate.evidence_status
        else None,
        "overall": candidate.scores.overall,
        "rank": candidate.rank,
        "status": candidate.status,
        "discard_reason": candidate.discard_reason,
    }


async def recent_history(
    factory: async_sessionmaker[AsyncSession], since: datetime
) -> list[PastTopic]:
    """Topics already written, in flight, promoted or turned down since ``since``.

    Pipeline runs stand in for articles: every article has one, and a run still
    queued is as much a duplicate as a published article. Failed runs are left
    out — they produced nothing. Both proposal tables count, so a research run
    does not re-propose what the MeSH scan's reviewer already promoted.
    """
    async with factory() as session:
        runs = await session.execute(
            select(PipelineRun.topic, PipelineRun.created_at).where(
                PipelineRun.created_at >= since, PipelineRun.status != RunStatus.FAILED
            )
        )
        research = await session.execute(
            select(
                ResearchCandidate.canonical_topic,
                ResearchCandidate.status,
                ResearchCandidate.decided_at,
            ).where(
                ResearchCandidate.decided_at >= since,
                ResearchCandidate.status.in_(
                    (ResearchCandidateStatus.PROMOTED, ResearchCandidateStatus.DISMISSED)
                ),
            )
        )
        discovery = await session.execute(
            select(
                DiscoveryCandidate.topic,
                DiscoveryCandidate.status,
                DiscoveryCandidate.decided_at,
            ).where(
                DiscoveryCandidate.decided_at >= since,
                or_(
                    DiscoveryCandidate.status == DiscoveryCandidateStatus.PROMOTED,
                    DiscoveryCandidate.status == DiscoveryCandidateStatus.DISMISSED,
                ),
            )
        )
    history = [PastTopic(text=topic, kind="article", decided_at=at) for topic, at in runs]
    for topic, status, at in [*research, *discovery]:
        history.append(PastTopic(text=topic, kind=str(status.value), decided_at=at))
    return history


# --- reads for the API -----------------------------------------------------


async def list_runs(session: AsyncSession, limit: int) -> list[ResearchRun]:
    return list(
        (
            await session.execute(
                select(ResearchRun).order_by(ResearchRun.created_at.desc()).limit(limit)
            )
        ).scalars()
    )


async def candidates_for(session: AsyncSession, run_id: str) -> list[ResearchCandidate]:
    return list(
        (
            await session.execute(
                select(ResearchCandidate)
                .where(ResearchCandidate.research_run_id == run_id)
                .order_by(
                    ResearchCandidate.rank.asc().nulls_last(), ResearchCandidate.canonical_topic
                )
            )
        ).scalars()
    )
