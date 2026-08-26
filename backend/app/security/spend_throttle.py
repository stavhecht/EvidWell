"""A budget for the one console action that bills an external provider.

Every other reviewer action costs a database round trip. Regenerating an
article's imagery costs two GPU renders on someone's hosted infrastructure,
charged to the account whose token is in ``IMAGE_GEN_KEY`` — so a held-down
button, a stuck retry loop, or a reviewer idly hunting for a nicer picture
spends real credit with nothing to stop it.

Three shapes borrowed deliberately from ``security/login_throttle.py``, and one
that differs:

**It rejects, it never waits.** Same argument as the login throttle, arriving
from the other direction: sleeping would hold a request open for the length of
the block, and the caller here is a browser waiting on a button. A 429 with
``Retry-After`` lets the console say "in 40 seconds" instead of hanging.

**It counts successes, not failures.** That is the difference, and it is why
this is not just another ``LoginThrottle`` instance. A login throttle counts
what went *wrong*, and a correct password clears the counter. Here the thing
worth counting is the thing that *worked* — a render that succeeded is the one
that was billed. A failed render costs nothing and is not counted, so a
provider outage cannot lock a reviewer out of retrying once it recovers.

**It is keyed by reviewer, not by IP.** The budget being protected is the
project's inference credit, which is per account and not per address, and
CLAUDE.md's rule about reviewers and readers sharing an office NAT cuts the
other way here: two reviewers behind one address should get two budgets, not
half of one each. The endpoint is authenticated, so there is a real identity to
key on — unlike ``/auth/login``, where there is not.

Per-process, like the login throttle, with the same honest caveat: N uvicorn
workers means N times the budget and a restart clears it. That is what the
Protocol exists to let you replace.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Budget:
    """Charged events tolerated inside a rolling window."""

    max_events: int
    window: timedelta


#: Loose enough that a reviewer working through a queue never notices, tight
#: enough that a stuck client cannot spend an afternoon's credit in a minute.
#:
#: **The unit is one render, not one press.** It was presses (12 of them, two
#: renders each) while every press drew both frames. A reviewer can now redraw
#: the article's picture without the tile's, and counting that as a whole press
#: would charge a half-price action at full rate — the wrong incentive on the
#: one control that exists to stop needless spending. 24 keeps the ceiling
#: exactly where it was.
REGENERATE_BUDGET = Budget(max_events=24, window=timedelta(minutes=10))

#: Ceiling on tracked keys. Reviewer ids are not attacker-controlled — the
#: endpoint is authenticated and the roster is small — so this is a guard
#: against a leak rather than against an attack, and evicting a spent entry is
#: always safe here.
MAX_TRACKED_KEYS = 1_000


@dataclass
class _KeyState:
    events: list[float] = field(default_factory=list)

    def prune(self, now: float, window: float) -> None:
        cutoff = now - window
        self.events = [stamp for stamp in self.events if stamp > cutoff]


class SpendThrottle(Protocol):
    """The seam. ``InMemorySpendThrottle`` is what runs."""

    def retry_after(self, key: str) -> float | None:
        """Seconds until this caller may spend again, or None if allowed."""
        ...

    def record(self, key: str, count: int = 1) -> None:
        """Count ``count`` charged events — one per render actually billed."""
        ...


class InMemorySpendThrottle:
    """Per-process counters in a dict.

    Every method is synchronous and never awaits, so under a single event loop
    each one is atomic and no lock is needed. Keep it that way.
    """

    def __init__(
        self,
        budget: Budget = REGENERATE_BUDGET,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._budget = budget
        self._state: dict[str, _KeyState] = {}
        #: Injected so tests can advance time instead of waiting out a window.
        #: Monotonic, so a clock adjustment cannot release a block early.
        self._clock = clock

    def retry_after(self, key: str) -> float | None:
        """How long until the oldest event in the window falls out of it.

        Checked *before* the provider call, so a blocked caller costs a dict
        read rather than a render.
        """
        now = self._clock()
        state = self._state.get(key)
        if state is None:
            return None

        window = self._budget.window.total_seconds()
        state.prune(now, window)
        if len(state.events) < self._budget.max_events:
            return None
        # The window frees up when the oldest event ages out of it.
        return max(0.0, state.events[0] + window - now)

    def record(self, key: str, count: int = 1) -> None:
        """Count ``count`` charged events against this reviewer.

        Called only after the renders actually succeeded. Counting attempts
        would let a provider outage burn a reviewer's budget on renders nobody
        was billed for.

        ``count`` is how many images were drawn, so a two-frame regenerate
        costs twice a one-frame one. It is charged *after* the fact, which
        means a caller sitting on their last allowed event can still spend two
        — the check above answers "is there any budget left", not "is there
        enough for this". A one-render overshoot on a 24-render window is not
        worth a reservation protocol; the ceiling this exists to defend is an
        order of magnitude, not a unit.
        """
        if count < 1:
            return
        now = self._clock()
        self._evict(now)
        state = self._state.get(key)
        if state is None:
            if len(self._state) >= MAX_TRACKED_KEYS:
                logger.error(
                    "spend throttle table full (%d keys); new reviewers are not "
                    "being counted until entries expire",
                    len(self._state),
                )
                return
            state = self._state[key] = _KeyState()
        state.prune(now, self._budget.window.total_seconds())
        state.events.extend([now] * count)

    def _evict(self, now: float) -> None:
        """Drop keys with nothing left inside the window."""
        if len(self._state) < MAX_TRACKED_KEYS:
            return
        window = self._budget.window.total_seconds()
        for key, state in list(self._state.items()):
            state.prune(now, window)
            if not state.events:
                del self._state[key]
