"""Editorial console endpoints. JWT required on every route.

Auth is applied at the router level (``dependencies=[Depends(require_reviewer)]``)
rather than per-handler. A per-handler decorator is one forgotten line away
from an unauthenticated console endpoint; a router-level dependency cannot be
forgotten by adding a route.

Two deliberate omissions from this surface:

* **No DELETE on articles.** Rejection is a state, and the audit trail is the
  product.
* **No endpoint that sets status directly.** ``approve`` and ``reject`` are the
  only transitions, and ``approve`` is the only path to ``published`` anywhere
  in the system.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select, tuple_

from app.api.console.schemas import (
    ArticleDetailOut,
    CreateRunRequest,
    LoginRequest,
    QueuePageOut,
    RejectRequest,
    ReviewerOut,
    RunOut,
    RunPageOut,
    SaveContentRequest,
    StageRunOut,
    TokenResponse,
)
from app.api.deps import ReviewerDep, SessionDep, SettingsDep, require_reviewer
from app.domain.enums import ArticleStatus, RunStatus, UserRole
from app.domain.models import PipelineRun, PipelineStageRun, User
from app.security.auth import (
    AuthenticatedReviewer,
    create_access_token,
    equalise_timing,
    verify_password,
)
from app.services.review import ReviewError, ReviewService

logger = logging.getLogger(__name__)

# Unauthenticated: login must be reachable without a token.
auth_router = APIRouter(prefix="/auth", tags=["console:auth"])

# Everything else.
router = APIRouter(dependencies=[Depends(require_reviewer)], tags=["console"])

_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Incorrect email or password",
    headers={"WWW-Authenticate": "Bearer"},
)


# --- auth ------------------------------------------------------------------


@auth_router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    """Exchange email + password for a bearer token.

    Identical 401 for unknown email and wrong password, and a dummy hash
    verification on the not-found branch so both cost the same — otherwise
    response timing enumerates valid reviewer accounts.
    """
    result = await session.execute(
        select(User).where(
            User.email == payload.email.strip().lower(), User.is_active.is_(True)
        )
    )
    user = result.scalar_one_or_none()

    if user is None:
        equalise_timing()
        raise _INVALID_CREDENTIALS

    if not verify_password(payload.password, user.password_hash):
        raise _INVALID_CREDENTIALS

    reviewer = AuthenticatedReviewer(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=UserRole(user.role),
    )
    token, expires_in = create_access_token(reviewer, settings.jwt_secret)
    logger.info("reviewer %s logged in", user.email)
    return TokenResponse(access_token=token, expires_in=expires_in)


@auth_router.get("/me", response_model=ReviewerOut, dependencies=[Depends(require_reviewer)])
async def me(reviewer: ReviewerDep) -> ReviewerOut:
    return ReviewerOut(
        id=reviewer.id,
        email=reviewer.email,
        display_name=reviewer.display_name,
        role=reviewer.role,
    )


# --- review queue ----------------------------------------------------------


@router.get("/articles", response_model=QueuePageOut)
async def list_queue(
    session: SessionDep,
    status_filter: Annotated[ArticleStatus, Query(alias="status")] = (
        ArticleStatus.PENDING_REVIEW
    ),
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> QueuePageOut:
    """The review queue, oldest first.

    ``status`` is a parameter so the console can also show the
    ``validation_failed`` tab — those drafts are never approvable, but they are
    the system's prompt-bug feedback loop, and hiding them makes invariant #2
    enforcement invisible.
    """
    try:
        items, next_cursor = await ReviewService(session).queue(status_filter, cursor, limit)
    except ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return QueuePageOut(items=items, next_cursor=next_cursor)


@router.get("/articles/{article_id}", response_model=ArticleDetailOut)
async def get_article_detail(article_id: str, session: SessionDep) -> ArticleDetailOut:
    """Draft, sources and validation report in one request.

    One request rather than three because the review screen renders the editor
    and the sources panel together — the "fast source access" requirement is
    about the reviewer's time, and a waterfall of requests spends it.
    """
    try:
        detail = await ReviewService(session).article_detail(article_id)
    except ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return ArticleDetailOut(**detail)


@router.patch("/articles/{article_id}/content", status_code=status.HTTP_204_NO_CONTENT)
async def save_content(
    article_id: str,
    payload: SaveContentRequest,
    session: SessionDep,
    reviewer: ReviewerDep,
) -> None:
    """Autosave editor content into ``edited_content``.

    Debounced ~800ms client-side, so this must be cheap and idempotent. Writes
    ``edited_content`` only; ``original_content`` is immutable and the DB
    trigger enforces that independently of this handler.
    """
    try:
        await ReviewService(session).save_edits(article_id, payload.content, reviewer.id)
    except ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/articles/{article_id}/approve", status_code=status.HTTP_204_NO_CONTENT)
async def approve(article_id: str, session: SessionDep, reviewer: ReviewerDep) -> None:
    """Publish. The only path to ``published`` in the entire system.

    409 when the article isn't in ``pending_review``, or when editing has
    orphaned a citation. The detail names the specific handles — a reviewer
    needs to know which citation broke, not merely that one did.
    """
    try:
        await ReviewService(session).approve(article_id, reviewer.id)
    except ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/articles/{article_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject(
    article_id: str,
    payload: RejectRequest,
    session: SessionDep,
    reviewer: ReviewerDep,
) -> None:
    """Reject with a required reason. Terminal."""
    try:
        await ReviewService(session).reject(article_id, reviewer.id, payload.reason)
    except ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# --- pipeline --------------------------------------------------------------


@router.post(
    "/pipeline/runs", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED
)
async def create_run(
    payload: CreateRunRequest, session: SessionDep, reviewer: ReviewerDep
) -> RunOut:
    """Enqueue a topic for draft generation.

    Inserts a ``queued`` run and returns immediately — generation takes
    minutes, so this is 202 and the client polls the runs list. The worker
    picks it up.

    Note that no response from this endpoint can ever produce a published
    article; the run's terminal state is a draft awaiting a human.
    """
    run = PipelineRun(
        topic=payload.topic,
        source_blurb=payload.blurb,
        status=RunStatus.QUEUED,
        requested_by=reviewer.id,
        created_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()
    logger.info("queued pipeline run %s for topic=%r", run.id, payload.topic)
    return _run_out(run, [])


@router.get("/pipeline/runs", response_model=RunPageOut)
async def list_runs(
    session: SessionDep,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> RunPageOut:
    """Run history with per-stage status, newest first."""
    statement = select(PipelineRun).order_by(
        PipelineRun.created_at.desc(), PipelineRun.id.desc()
    )

    if cursor:
        anchor = await session.get(PipelineRun, cursor)
        if anchor is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed cursor"
            )
        statement = statement.where(
            tuple_(PipelineRun.created_at, PipelineRun.id) < (anchor.created_at, anchor.id)
        )

    result = await session.execute(statement.limit(limit + 1))
    runs = list(result.scalars())

    next_cursor = None
    if len(runs) > limit:
        runs = runs[:limit]
        next_cursor = runs[-1].id

    stages_by_run = await _load_stages(session, [run.id for run in runs])
    return RunPageOut(
        items=[_run_out(run, stages_by_run.get(run.id, [])) for run in runs],
        next_cursor=next_cursor,
    )


@router.get("/pipeline/runs/{run_id}", response_model=RunOut)
async def get_run(run_id: str, session: SessionDep) -> RunOut:
    """One run: every stage, its error payload, timing and token cost.

    This is the answer to "why did last night's batch produce nothing" —
    without it, a validation failure and a crashed provider look identical from
    the outside.
    """
    run = await session.get(PipelineRun, run_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    stages_by_run = await _load_stages(session, [run_id])
    return _run_out(run, stages_by_run.get(run_id, []))


async def _load_stages(
    session: SessionDep, run_ids: list[str]
) -> dict[str, list[PipelineStageRun]]:
    if not run_ids:
        return {}
    result = await session.execute(
        select(PipelineStageRun)
        .where(PipelineStageRun.run_id.in_(run_ids))
        .order_by(PipelineStageRun.ordinal)
    )
    grouped: dict[str, list[PipelineStageRun]] = {}
    for stage in result.scalars():
        grouped.setdefault(stage.run_id, []).append(stage)
    return grouped


def _run_out(run: PipelineRun, stages: list[PipelineStageRun]) -> RunOut:
    return RunOut(
        id=run.id,
        topic=run.topic,
        status=run.status,
        article_id=run.article_id,
        error=run.error,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        stages=[
            StageRunOut(
                stage=stage.stage,
                ordinal=stage.ordinal,
                status=stage.status,
                error=stage.error,
                metrics=stage.metrics,
                started_at=stage.started_at,
                finished_at=stage.finished_at,
            )
            for stage in stages
        ],
        created_at=run.created_at,
        finished_at=run.finished_at,
    )
