"""Client-side pacing for the scholarly APIs.

Rate limiting matters more here than in most fan-outs, because of what a 429
becomes downstream. A throttled provider raises, ``RetrieveStage`` logs a
warning and carries on, recall drops, and a thinner evidence base yields a
*more cautious* verdict — which in the finished article is indistinguishable
from a correct one. Now that ``SynthesizeStage`` writes a real ``no_evidence``
article instead of crashing (DESIGN.md §5), the worst case is publishing a
confident empty verdict about a claim whose search never actually ran.

So the pacing is deliberately conservative, and its two halves are separate
concerns:

* ``RateLimiter`` keeps us under the ceiling. It is per provider *per process*,
  while every limit here is documented per IP — two workers on one host share
  the ceiling and neither can see the other, which is why the configured rates
  in ``factory.py`` sit below the published ones.
* ``ThrottledClient`` handles being over it anyway: honour ``Retry-After``,
  retry a bounded number of times, then raise ``RateLimited`` rather than
  returning something a provider would parse as "no results".
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable, Mapping
from typing import Any, Protocol

import httpx

from app.retrieval.base import RateLimited

logger = logging.getLogger(__name__)

#: First retry waits this long, doubling per attempt. Only used as a floor —
#: a server that tells us how long to wait always wins.
BASE_BACKOFF_SECONDS = 0.5

#: Added to every backoff. Without it, tasks throttled together wake together
#: and immediately throttle each other again.
MAX_JITTER_SECONDS = 0.25


class HttpClient(Protocol):
    """The slice of ``httpx.AsyncClient`` the providers actually use.

    Narrow on purpose: it is what lets ``ThrottledClient`` stand in for the raw
    client without any provider knowing, and what lets a test pass a scripted
    stub without a transport.
    """

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response: ...


#: Returns the seconds to wait, or ``None`` when the response is not a
#: throttle. ``0.0`` means "throttled, no hint how long".
ThrottleDetector = Callable[[httpx.Response], float | None]


class RateLimiter:
    """Token bucket. One per provider, shared by every call to that provider.

    Applied at the HTTP-call level, not around ``search()``. A PubMed search is
    two requests — esearch then efetch — and the published ceiling counts
    requests, so pacing searches at 3/s issues 6/s.
    """

    def __init__(self, per_second: float, burst: int = 1) -> None:
        if per_second <= 0:
            raise ValueError(f"per_second must be positive, got {per_second!r}")
        self._interval = 1.0 / per_second
        self._burst = max(1.0, float(burst))
        self._tokens = self._burst
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait until a request may be issued.

        The lock is held *across* the sleep, which looks wrong and is not.
        Waiters queue in arrival order and each computes its wait after the
        previous one has taken its token. Release first and every waiter reads
        the same empty bucket, sleeps the same interval, and fires
        simultaneously — reproducing the exact burst this class prevents.
        """
        async with self._lock:
            self._refill()
            if self._tokens < 1.0:
                await asyncio.sleep((1.0 - self._tokens) * self._interval)
                self._refill()
            self._tokens -= 1.0

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        self._tokens = min(self._burst, self._tokens + elapsed / self._interval)
        self._updated = now


class ThrottledClient:
    """Paces one provider's requests and retries the ones it throttles.

    Wraps the shared ``httpx.AsyncClient`` rather than replacing it, so
    connection pooling stays process-wide while pacing stays per provider.
    Providers see what they had before: ``get`` returns a response, or raises.
    """

    def __init__(
        self,
        http: HttpClient,
        limiter: RateLimiter,
        *,
        provider: str,
        max_retries: int = 2,
        max_wait_seconds: float = 10.0,
        detect_throttle: ThrottleDetector | None = None,
    ) -> None:
        """
        Args:
            detect_throttle: Provider-specific throttle signal. 429 is handled
                here for everyone; this covers APIs that say it some other way.
                PubMed answers 200 with an error string in the body, and
                without a detector that response parses as zero results — the
                single most misleading thing this system can conclude.
        """
        self._http = http
        self._limiter = limiter
        self._provider = provider
        self._max_retries = max_retries
        self._max_wait_seconds = max_wait_seconds
        self._detect = detect_throttle

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """GET, paced, retrying while the provider says it is throttling us.

        Raises:
            RateLimited: still throttled after ``max_retries`` retries. Carries
                the last ``retry_after`` so the caller can report how long the
                provider wanted rather than guessing.
            httpx.HTTPError: transport failure, unretried — providers already
                wrap these, and a retry loop here would double the one they may
                add later.
        """
        retry_after = 0.0
        for attempt in range(self._max_retries + 1):
            await self._limiter.acquire()
            response = await self._http.get(url, params=params, headers=headers)

            signal = self._throttle_signal(response)
            if signal is None:
                return response
            retry_after = signal

            if attempt < self._max_retries:
                delay = self._backoff(attempt, retry_after)
                logger.warning(
                    "%s rate limited; waiting %.2fs then retrying (%d of %d)",
                    self._provider,
                    delay,
                    attempt + 1,
                    self._max_retries,
                )
                await asyncio.sleep(delay)

        raise RateLimited(
            f"{self._provider} still rate limiting after "
            f"{self._max_retries + 1} attempts",
            retry_after=retry_after or None,
        )

    def _throttle_signal(self, response: httpx.Response) -> float | None:
        if response.status_code == 429:
            return retry_after_seconds(response)
        return self._detect(response) if self._detect else None

    def _backoff(self, attempt: int, retry_after: float) -> float:
        """Never shorter than the provider asked, never flat across attempts.

        Taking the max of the two means a missing ``Retry-After`` (0.0) falls
        back to exponential without a special case, and a server asking for
        longer than our own schedule is obeyed.
        """
        exponential: float = BASE_BACKOFF_SECONDS * (2.0**attempt)
        delay = max(retry_after, exponential) + random.uniform(0, MAX_JITTER_SECONDS)
        return min(delay, self._max_wait_seconds)


def retry_after_seconds(response: httpx.Response) -> float:
    """``Retry-After`` in seconds; 0.0 when absent or unparseable.

    The HTTP-date form of the header is legal and none of these four APIs
    sends it. Treating it as absent falls back to exponential backoff, which is
    a better failure than a parser for a format we never see.
    """
    raw: str = response.headers.get("retry-after", "")
    try:
        return max(0.0, float(raw))
    except ValueError:
        return 0.0
