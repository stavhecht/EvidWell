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

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select, tuple_

from app.api.console.schemas import (
    ArticleDetailOut,
    ContactRequestOut,
    CreateRunRequest,
    LoginRequest,
    MediaUploadOut,
    QueuePageOut,
    RejectRequest,
    ReviewerOut,
    RunOut,
    RunPageOut,
    SaveContentRequest,
    SetContactStatusRequest,
    SetSubjectRequest,
    StageRunOut,
    TokenResponse,
)
from app.api.deps import (
    ClientIpDep,
    LoginThrottleDep,
    ReviewerDep,
    SessionDep,
    SettingsDep,
    require_reviewer,
)
from app.domain.enums import ArticleStatus, ContactStatus, RunStatus, UserRole
from app.domain.models import PipelineRun, PipelineStageRun, User
from app.llm.base import TokenUsage
from app.llm.pricing import cost_usd, total_cost_usd
from app.security.auth import (
    AuthenticatedReviewer,
    create_access_token,
    equalise_timing,
    verify_password,
)
from app.services.contact import ContactError, ContactService
from app.services.media import UnsupportedMediaError, store_image
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
    payload: LoginRequest,
    session: SessionDep,
    settings: SettingsDep,
    throttle: LoginThrottleDep,
    ip: ClientIpDep,
) -> TokenResponse:
    """Exchange email + password for a bearer token.

    Identical 401 for unknown email and wrong password, and a dummy hash
    verification on the not-found branch so both cost the same — otherwise
    response timing enumerates valid reviewer accounts.

    **The throttle check comes first, before the query and before any
    hashing.** Argon2id is memory-hard by design, so an unthrottled login
    endpoint is a CPU amplifier as much as a password oracle — and
    ``equalise_timing`` means junk input costs the same as a real attempt.
    Checking after the lookup would protect the password and not the process.

    Failures are counted for an unknown email exactly as for a wrong password.
    Counting only real accounts would make the 429 an existence oracle and
    give back the enumeration resistance the equal timing buys.
    """
    email = payload.email.strip().lower()

    retry_after = throttle.retry_after(ip=ip, email=email)
    if retry_after is not None:
        logger.warning("login refused for %r from %s: throttled", email, ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again later.",
            headers={"Retry-After": str(max(1, int(retry_after)))},
        )

    result = await session.execute(
        select(User).where(User.email == email, User.is_active.is_(True))
    )
    user = result.scalar_one_or_none()

    if user is None:
        equalise_timing()
        throttle.record_failure(ip=ip, email=email)
        raise _INVALID_CREDENTIALS

    if not verify_password(payload.password, user.password_hash):
        throttle.record_failure(ip=ip, email=email)
        raise _INVALID_CREDENTIALS

    throttle.record_success(ip=ip, email=email)
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


@router.patch("/articles/{article_id}/subject", status_code=status.HTTP_204_NO_CONTENT)
async def set_subject(
    article_id: str,
    payload: SetSubjectRequest,
    session: SessionDep,
    reviewer: ReviewerDep,
) -> None:
    """Classify what kind of thing the article assesses.

    Separate from the content PATCH, and not part of approve, because it is the
    one field a reviewer may legitimately change *after* publication: it drives
    a colour and a browse listing rather than a word the reader was shown, and
    getting it wrong should be correctable without touching the article.
    """
    try:
        await ReviewService(session).set_subject(article_id, payload.subject, reviewer.id)
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
    """Reject with a required reason. Terminal.

    Reachable from ``pending_review`` and from ``validation_failed``. The
    latter is the console's discard action: with no DELETE on articles this is
    the only way a draft leaves a queue tab, and a failed draft with no
    available action simply accumulates.
    """
    try:
        await ReviewService(session).reject(article_id, reviewer.id, payload.reason)
    except ReviewError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


# --- media -----------------------------------------------------------------


@router.post("/media", response_model=MediaUploadOut, status_code=status.HTTP_201_CREATED)
async def upload_media(
    file: Annotated[UploadFile, File(description="A PNG, JPEG, GIF or WebP image")],
    settings: SettingsDep,
    reviewer: ReviewerDep,
) -> MediaUploadOut:
    """Store an image the reviewer picked on their own machine.

    Not scoped to an article. The store is content-addressed and an image is
    referenced only by the document that embeds it, so an article id here would
    be a claim about ownership that nothing could keep true once the reviewer
    moves the image between drafts.

    The upload's filename and Content-Type are never trusted: the response
    reports the type sniffed from the bytes, and the stored filename is their
    digest. See ``services/media.py`` for why that matters when the same
    directory is served back over HTTP.

    413 when the file is over the ceiling, 415 when the bytes are not one of
    the four formats a browser can render without executing anything.
    """
    ceiling = settings.media_max_bytes
    # Read one byte past the ceiling: enough to know it was exceeded, never
    # enough for an oversized upload to be buffered whole.
    data = await file.read(ceiling + 1)
    if len(data) > ceiling:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Images must be under {ceiling // (1024 * 1024)} MB.",
        )
    if not data:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="The file was empty."
        )

    try:
        # Synchronous write: bounded by the ceiling above, to local disk, from
        # a console action a reviewer takes a handful of times per article. The
        # S3 version of this call is async and belongs in the module that owns
        # the store, not in a thread pool here.
        stored = store_image(data, root=settings.media_root)
    except UnsupportedMediaError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc

    logger.info(
        "reviewer %s uploaded %s (%d bytes) as %s",
        reviewer.email,
        stored.content_type,
        stored.size,
        stored.src,
    )
    return MediaUploadOut(
        src=stored.src, content_type=stored.content_type, bytes=stored.size
    )


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
        # Attempts are counted when a worker claims the run, so a queued one
        # has had none. Set explicitly rather than left to the server default,
        # which is not populated on the instance until it is re-read.
        attempts=0,
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
    """Stage rows for each run — the latest attempt only.

    A retried run holds one set of rows per attempt (they are kept, not
    overwritten, so a run that succeeded on its third try still shows what the
    first two did). Returning all of them would render the pipeline as six
    stages repeated N times, which reads as a bug. The console's question is
    "where is this run now", and the latest attempt answers it; ``attempts`` on
    the run says how many there were, and the earlier rows stay in the table
    for anyone debugging.
    """
    if not run_ids:
        return {}
    result = await session.execute(
        select(PipelineStageRun)
        .where(PipelineStageRun.run_id.in_(run_ids))
        .order_by(PipelineStageRun.attempt, PipelineStageRun.ordinal)
    )
    grouped: dict[str, list[PipelineStageRun]] = {}
    latest: dict[str, int] = {}
    for stage in result.scalars():
        # Ordered by attempt, so a higher one supersedes what we have so far.
        if stage.attempt > latest.get(stage.run_id, 0):
            latest[stage.run_id] = stage.attempt
            grouped[stage.run_id] = []
        grouped.setdefault(stage.run_id, []).append(stage)
    return grouped


def _stage_usage(stage: PipelineStageRun) -> TokenUsage:
    return TokenUsage(
        input_tokens=stage.input_tokens,
        output_tokens=stage.output_tokens,
        cache_read_tokens=stage.cache_read_tokens,
        cache_write_tokens=stage.cache_write_tokens,
    )


def _run_out(run: PipelineRun, stages: list[PipelineStageRun]) -> RunOut:
    # Cost is derived here rather than stored, so a price correction fixes
    # history instead of leaving it wrong — see app/llm/pricing.py. Summed from
    # the stage rows because the run's own totals span models and cannot be
    # priced; note the stage list is the *latest attempt only*, so a retried
    # run's cost is deliberately lower than its token totals imply.
    stage_costs = total_cost_usd(
        [(stage.model or "", _stage_usage(stage)) for stage in stages]
    )
    return RunOut(
        id=run.id,
        topic=run.topic,
        status=run.status,
        article_id=run.article_id,
        error=run.error,
        input_tokens=run.input_tokens,
        output_tokens=run.output_tokens,
        cache_read_tokens=run.cache_read_tokens,
        cache_write_tokens=run.cache_write_tokens,
        estimated_cost_usd=stage_costs,
        attempts=run.attempts,
        next_attempt_at=run.next_attempt_at,
        heartbeat_at=run.heartbeat_at,
        stages=[
            StageRunOut(
                stage=stage.stage,
                ordinal=stage.ordinal,
                status=stage.status,
                error=stage.error,
                metrics=stage.metrics,
                model=stage.model,
                input_tokens=stage.input_tokens,
                output_tokens=stage.output_tokens,
                cache_read_tokens=stage.cache_read_tokens,
                cache_write_tokens=stage.cache_write_tokens,
                estimated_cost_usd=cost_usd(stage.model or "", _stage_usage(stage)),
                started_at=stage.started_at,
                finished_at=stage.finished_at,
            )
            for stage in stages
        ],
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


# --- contact inbox ---------------------------------------------------------
#
# The reading half of "Let us know". The writing half is a public route; this
# side is reviewer-only because the rows carry an email address and free text
# written by anyone with the URL.


@router.get("/contact", response_model=list[ContactRequestOut])
async def contact_inbox(
    session: SessionDep,
    contact_status: Annotated[
        ContactStatus | None,
        Query(alias="status", description="Filter by handling state"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[ContactRequestOut]:
    """Newest first. Deliberately unpaginated — see ``ContactService.inbox``."""
    rows = await ContactService(session).inbox(contact_status, limit)
    return [ContactRequestOut(**row) for row in rows]


@router.patch("/contact/{request_id}", status_code=status.HTTP_204_NO_CONTENT)
async def set_contact_status(
    request_id: str,
    payload: SetContactStatusRequest,
    session: SessionDep,
    reviewer: ReviewerDep,
) -> None:
    """Mark a request answered or closed, or put it back in the queue.

    Reversible on purpose: closing a request is bookkeeping, not a decision
    anyone is answerable for, and the trail of who last touched it is kept
    either way.
    """
    try:
        await ContactService(session).set_status(request_id, payload.status, reviewer.id)
    except ContactError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
