"""Runs the trend scan on a timer so the desk fills without a button press.

**This schedules a scan, and a scan proposes.** Nothing here enqueues a run or
spends a token: the generative bill still sits entirely behind a reviewer
choosing to promote a candidate, which is invariant #1's reasoning one step
earlier (CLAUDE.md, "Trend discovery proposes; it never enqueues"). That is what
makes automating this side safe to leave on by default — the thing being
automated costs PubMed requests and nothing of ours.

Four things about it are deliberate.

**It goes through ``manual_scan``, the same single-flight runner the button
uses.** Not a second code path: a scheduled scan and a pressed one then cannot
overlap, because the runner already refuses to start twice, and the desk's
status line describes whichever one is in flight without knowing which started
it. Two concurrent scans would double this process's draw on NCBI's per-IP
ceiling — shared with the worker — to compute the same answer twice.

**Due-ness is measured from the ledger's last succeeded scan, never from a timer
since process start.** A restart therefore does not trigger a scan, a deploy
loop does not trigger one per deploy, and a slot missed while the process was
down heals on the next tick instead of waiting a full interval. Same shape as
`discovery_overlap_days`, and the reason `discovery_scans` is the window ledger
rather than a log.

**The loop must never raise.** A scheduler that dies takes the desk back to
manual without saying so, and the failure is invisible precisely because the
symptom is *nothing happening*. Every tick is wrapped; a failing scan is already
recorded on the runner's state and surfaces on the desk.

**It runs in the API process, because that is where ``manual_scan`` lives.** One
runner, one guard. The consequence is that running more than one API replica
would give each its own scheduler and its own single-flight scope — wasteful
rather than corrupting, because the observation ledger is idempotent, but if
this is ever scaled out the schedule wants moving behind an advisory lock.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress
from datetime import UTC, datetime, timedelta

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.discovery.manual import ScanAlreadyRunning, manual_scan
from app.domain.enums import DiscoveryScanStatus
from app.domain.models import DiscoveryScan

logger = logging.getLogger(__name__)

#: How often to ask "is a scan due?". Not the scan interval — this is the
#: resolution at which the answer is checked, and it is short so that a missed
#: slot is picked up promptly after a restart. The check is one ``MAX()`` over a
#: small table, so a tight tick costs nothing.
TICK_SECONDS = 15 * 60


async def last_succeeded_at(session: AsyncSession) -> datetime | None:
    """When a scan last finished successfully — cron, console or CLI alike.

    Reads only succeeded rows, matching ``next_window``: a failed scan must not
    count as coverage, or a provider outage would silently advance the window
    and leave a fortnight of the literature unharvested.
    """
    return await session.scalar(
        select(sa_func.max(DiscoveryScan.finished_at)).where(
            DiscoveryScan.status == DiscoveryScanStatus.SUCCEEDED
        )
    )


def is_due(last: datetime | None, interval: timedelta, *, now: datetime) -> bool:
    """True when the last succeeded scan is older than the interval.

    ``None`` — no scan has ever succeeded — is due: a fresh install should fill
    its desk without someone finding the button first.

    Rows written before timestamps carried a timezone are treated as UTC rather
    than crashing the comparison, because a naive datetime here would take the
    scheduler down permanently on a database that predates the change, and the
    worst case of the assumption is one extra scan.
    """
    if last is None:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    return now - last >= interval


class ScanScheduler:
    """Owns the timer task. Started and stopped by the app lifespan."""

    def __init__(
        self,
        settings: Settings,
        factory: async_sessionmaker[AsyncSession],
        *,
        tick_seconds: float = TICK_SECONDS,
    ) -> None:
        self._settings = settings
        self._factory = factory
        self._tick = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if not self._settings.discovery_scan_enabled:
            logger.info("trend scan scheduler disabled (discovery_scan_enabled=false)")
            return
        # Held rather than fire-and-forget: the event loop keeps only weak
        # references to tasks, so an unreferenced one can be collected mid-wait
        # and simply stop. Same reason `ManualScanRunner` holds its own.
        self._task = asyncio.create_task(self._run())
        logger.info(
            "trend scan scheduler started (every %dh, checked every %.0fm)",
            self._settings.discovery_scan_interval_hours,
            self._tick / 60,
        )

    async def stop(self) -> None:
        self._stopping.set()
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    async def _run(self) -> None:
        interval = timedelta(hours=self._settings.discovery_scan_interval_hours)
        while not self._stopping.is_set():
            try:
                await self._tick_once(interval)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never fatal. The symptom of a dead scheduler is nothing
                # happening, which is indistinguishable from a quiet fortnight,
                # so it must not be reachable by one bad tick.
                logger.exception("trend scan scheduler tick failed; continuing")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._tick)
            except TimeoutError:
                continue

    async def _tick_once(self, interval: timedelta) -> None:
        if manual_scan.is_running:
            return
        async with self._factory() as session:
            last = await last_succeeded_at(session)
        if not is_due(last, interval, now=datetime.now(UTC)):
            return
        try:
            manual_scan.start(settings=self._settings, factory=self._factory)
        except ScanAlreadyRunning:
            # Raced with a press between the check above and here. The button
            # wins; this tick simply does nothing and the next one re-checks.
            return
        logger.info("trend scan started on schedule (last succeeded %s)", last)
