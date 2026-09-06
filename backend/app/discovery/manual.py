"""The console's "run the scan now" button, server side.

A reviewer who has just dismissed the last proposal, or who has been told a new
study landed, should not have to wait for Monday's cron slot. This is that
button — and it is safe to hand over for one structural reason: **a scan
proposes and nothing else.** It makes no model calls, enqueues no run and spends
no tokens; the only cost is PubMed requests against a rate limiter the pipeline
already shares. The expensive, human-owned decision stays where it was, on
Generate draft.

Four decisions worth keeping.

**In-process, not a subprocess.** ``scripts/`` is deliberately outside the
deployed image, so shelling out to ``python -m scripts.scan_trends`` would work
on a laptop and 500 in a container. Both callers share
``discovery/scan.py::run_scan`` instead.

**Single-flight, per process.** Two concurrent scans would double this process's
share of NCBI's per-IP ceiling — which the worker is also drawing on — for two
copies of the same answer. The guard is a task handle, so it is per *process*
and would not stop a second API replica; that is acceptable rather than
overlooked, because the ledger is idempotent (observations are keyed
``(descriptor, pmid)`` and candidates upsert), so the failure mode of a
concurrent scan is wasted requests, never a corrupted baseline.

**State lives here, not in `discovery_scans`.** That table is the *window*
ledger — ``next_window`` reads only its succeeded rows, and a failed scan must
not move the mark. Recording a press there would either pollute that index or
require a second status vocabulary on top of it. This is a UI affordance and it
is honest about being one: a restart forgets that a scan was running, and the
next poll reports ``idle``.

**The task can never fail the request that started it.** ``start`` returns as
soon as the task is scheduled; everything the scan raises is caught and lands on
the state as an error string for the next poll. A reviewer pressing a button
must not be shown a 500 from PubMed twenty seconds later.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.discovery.scan import run_scan

logger = logging.getLogger(__name__)


class ManualScanStatus(StrEnum):
    """Where the button is.

    ``IDLE`` is the resting state *and* the state after a restart — see the
    module docstring. It is not "never scanned"; ``last_finished_at`` on the
    response answers that, from the ledger.
    """

    IDLE = "idle"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ScanAlreadyRunning(RuntimeError):
    """Raised by ``start`` when this process is already scanning."""


@dataclass(frozen=True)
class ManualScanState:
    """What the desk is told about the last press.

    Carries ``records_seen`` as well as the candidate count because zero
    candidates is an ordinary outcome — most fortnights propose nothing new —
    while zero *records* means the harvest itself came back empty, and a single
    number cannot tell those apart.
    """

    status: ManualScanStatus = ManualScanStatus.IDLE
    started_at: datetime | None = None
    finished_at: datetime | None = None
    records_seen: int = 0
    observations_written: int = 0
    candidates_proposed: int = 0
    #: The scan's own remarks — a shallow baseline, a truncated seed. Shown
    #: verbatim: they are the difference between "nothing is trending" and
    #: "this could not have proposed anything".
    notes: list[str] = field(default_factory=list)
    error: str | None = None


class ManualScanRunner:
    """One scan at a time, held for the lifetime of the process."""

    def __init__(self) -> None:
        self._state = ManualScanState()
        # Held rather than fire-and-forget: the event loop keeps only weak
        # references to tasks, so an unreferenced one can be garbage collected
        # mid-scan and simply stop.
        self._task: asyncio.Task[None] | None = None

    @property
    def state(self) -> ManualScanState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(
        self, *, settings: Settings, factory: async_sessionmaker[AsyncSession]
    ) -> ManualScanState:
        if self.is_running:
            raise ScanAlreadyRunning("a scan is already running")
        self._state = ManualScanState(
            status=ManualScanStatus.RUNNING, started_at=datetime.now(UTC)
        )
        self._task = asyncio.create_task(self._run(settings, factory))
        return self._state

    async def _run(
        self, settings: Settings, factory: async_sessionmaker[AsyncSession]
    ) -> None:
        started = self._state.started_at
        try:
            # `apply=True`: a dry run from the desk would make every PubMed
            # request and write nothing, which is the one shape of this button
            # nobody wants.
            report = await run_scan(settings=settings, factory=factory, apply=True)
        except Exception as exc:  # the state is the report; nothing above catches
            logger.exception("manual trend scan failed")
            self._state = ManualScanState(
                status=ManualScanStatus.FAILED,
                started_at=started,
                finished_at=datetime.now(UTC),
                error=f"{type(exc).__name__}: {exc}",
            )
            return
        self._state = ManualScanState(
            status=ManualScanStatus.SUCCEEDED,
            started_at=started,
            finished_at=datetime.now(UTC),
            records_seen=report.records_seen,
            observations_written=report.observations_written,
            candidates_proposed=report.candidates_proposed,
            notes=list(report.notes),
        )


#: Process-wide, imported by the console router. See "Single-flight" above.
manual_scan = ManualScanRunner()
