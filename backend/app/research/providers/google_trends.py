"""Google Trends, through pytrends. The only file that imports it.

Google offers no free official Trends API. pytrends scrapes the same endpoints
the Explore page uses; its repository was archived in April 2025, but
``related_queries`` and ``interest_over_time`` still answered when this was
built (2026-09-30). Two things were measured then and shape this file:

* **The first request is often a 429**, and a pause of ~20 s clears it. So
  every call is paced (``min_interval``) and a 429 is retried after a growing
  wait, a bounded number of times, before the provider reports itself
  unavailable. Nothing is invented when it gives up.
* **pytrends' own ``retries`` crash under urllib3 2** (``method_whitelist`` was
  renamed), so its retry option is left at 0 and retrying happens here.

pytrends is synchronous (requests + pandas), so each call runs in a thread,
one at a time behind a lock — Google rate-limits per IP, and parallel calls
would only buy more 429s.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.research.providers.base import (
    ProviderUnavailable,
    RelatedQueries,
    RelatedQuery,
)

logger = logging.getLogger(__name__)

#: Google labels a rising query "Breakout" above this percent growth.
BREAKOUT_PERCENT = 5000

#: Waits after a 429, in seconds. The measured cure was a ~20 s pause.
_BACKOFF = (15.0, 45.0)


def related_timeframe(window_days: int) -> str:
    """The Explore timeframe whose "rising" compares against the window."""
    return "now 7-d" if window_days <= 7 else "today 1-m"


def interest_timeframe(days: int) -> str:
    """A daily series covering at least ``days``: two trend windows."""
    return "today 1-m" if days <= 30 else "today 3-m"


class GoogleTrendsProvider:
    name = "google_trends"

    def __init__(
        self,
        *,
        min_interval: float = 4.0,
        client_factory: Callable[[], Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        backoff: tuple[float, ...] = _BACKOFF,
    ) -> None:
        self._min_interval = min_interval
        self._client_factory = client_factory or _default_client
        self._client: Any = None
        self._lock = asyncio.Lock()
        self._last_call = 0.0
        self._sleep = sleep
        self._backoff = backoff

    async def related_queries(self, seed: str, *, window_days: int, geo: str) -> RelatedQueries:
        def call(client: Any) -> Any:
            client.build_payload(
                [seed], cat=0, timeframe=related_timeframe(window_days), geo=geo
            )
            return client.related_queries()

        result = await self._call(call, f"related queries for {seed!r}")
        frames = (result or {}).get(seed) or {}
        return RelatedQueries(
            rising=_rows(frames.get("rising")),
            top=_rows(frames.get("top")),
        )

    async def interest_over_time(
        self, queries: list[str], *, days: int, geo: str
    ) -> dict[str, list[float]]:
        if not 1 <= len(queries) <= 5:
            raise ValueError("Google Trends compares one to five queries per request")

        def call(client: Any) -> Any:
            client.build_payload(
                list(queries), cat=0, timeframe=interest_timeframe(days), geo=geo
            )
            return client.interest_over_time()

        frame = await self._call(call, f"interest for {len(queries)} queries")
        return _series(frame, queries)

    async def _call(self, fn: Callable[[Any], Any], what: str) -> Any:
        async with self._lock:
            attempts = len(self._backoff) + 1
            for attempt in range(1, attempts + 1):
                wait = self._min_interval - (time.monotonic() - self._last_call)
                if wait > 0:
                    await self._sleep(wait)
                try:
                    return await asyncio.to_thread(self._invoke, fn)
                except Exception as exc:  # pytrends raises requests/pandas errors too
                    throttled = _is_throttle(exc)
                    if throttled and attempt < attempts:
                        delay = self._backoff[attempt - 1]
                        logger.info(
                            "google trends throttled on %s (attempt %d/%d); waiting %.0fs",
                            what,
                            attempt,
                            attempts,
                            delay,
                        )
                        # A throttled session's cookies are worth nothing.
                        self._client = None
                        await self._sleep(delay)
                        continue
                    raise ProviderUnavailable(
                        f"google trends {what} failed: {type(exc).__name__}: {exc}"
                    ) from exc
                finally:
                    self._last_call = time.monotonic()
        raise ProviderUnavailable(
            f"google trends {what}: retries exhausted"
        )  # pragma: no cover

    def _invoke(self, fn: Callable[[Any], Any]) -> Any:
        if self._client is None:
            self._client = self._client_factory()
        return fn(self._client)


def _default_client() -> Any:
    from pytrends.request import TrendReq

    # retries=0: pytrends' Retry(method_whitelist=...) raises TypeError under
    # urllib3 2. Retrying is done in `_call` instead.
    return TrendReq(hl="en-US", tz=0, timeout=(10, 25), retries=0)


def _is_throttle(exc: BaseException) -> bool:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status == 429 or "429" in str(exc) or type(exc).__name__ == "TooManyRequestsError"


def _rows(frame: Any) -> list[RelatedQuery]:
    """A pytrends (query, value) frame as plain rows. ``None`` means none found."""
    if frame is None:
        return []
    rows: list[RelatedQuery] = []
    for record in frame.to_dict("records"):
        query = str(record.get("query") or "").strip()
        value = record.get("value")
        if not query:
            continue
        try:
            rows.append(RelatedQuery(query=query, value=int(value)))
        except (TypeError, ValueError):
            continue
    return rows


def _series(frame: Any, queries: list[str]) -> dict[str, list[float]]:
    """Per-query daily values, dropping a trailing partial day.

    A partial last day reads as a dip for every query alike, which would make
    every candidate look like it is cooling off.
    """
    if frame is None or getattr(frame, "empty", True):
        # Google answers an all-zero comparison with an empty frame; that is a
        # measured "no interest", not a failure.
        return {query: [] for query in queries}
    records = frame.to_dict("records")
    if records and records[-1].get("isPartial"):
        records = records[:-1]
    return {query: [float(record.get(query) or 0.0) for record in records] for query in queries}
