"""News coverage: News API when a key is configured, DuckDuckGo news otherwise.

Coverage is a signal of what is being discussed, never of what is true. The
research agent counts articles in two windows (momentum) and keeps a few titles
for the reviewer; it never reads an article as evidence.

News API's free developer plan (as of 2026-09): 100 requests a day, results
delayed 24 hours, a month of history, development use only. One request per
candidate keeps a run at 50 or fewer. When it is throttled, out of quota or
down, ``FallbackNewsProvider`` asks DuckDuckGo news instead, and the signal
records which source answered.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.research.contracts import NewsArticle
from app.research.providers.base import NewsProvider, NewsResults, ProviderUnavailable
from app.research.providers.web_search import _call_ddgs, _ddgs
from app.retrieval.base import ProviderError
from app.retrieval.throttle import HttpClient

logger = logging.getLogger(__name__)

NEWS_API_URL = "https://newsapi.org/v2/everything"
#: News API's page ceiling. A run makes one request per candidate, so a topic
#: with more articles than this in the window reports ``previous`` as unknown.
NEWS_API_PAGE_SIZE = 100
DDG_NEWS_MAX_RESULTS = 50


class NewsApiProvider:
    name = "news_api"

    def __init__(self, http: HttpClient, api_key: str) -> None:
        self._http = http
        self._api_key = api_key

    async def search(
        self, terms: list[str], *, since: date, until: date, language: str
    ) -> NewsResults:
        params = {
            "q": news_api_query(terms),
            "from": since.isoformat(),
            "to": until.isoformat(),
            "language": language,
            "sortBy": "publishedAt",
            "pageSize": str(NEWS_API_PAGE_SIZE),
        }
        try:
            response = await self._http.get(
                NEWS_API_URL, params=params, headers={"X-Api-Key": self._api_key}
            )
        except (httpx.HTTPError, ProviderError) as exc:
            raise ProviderUnavailable(f"news api request failed: {exc}") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnavailable("news api returned non-JSON") from exc
        if response.status_code >= 400 or payload.get("status") != "ok":
            # The body names the reason (rateLimited, apiKeyInvalid,
            # parameterInvalid); the key itself never appears in it.
            raise ProviderUnavailable(
                f"news api answered {response.status_code}: "
                f"{payload.get('code')}: {payload.get('message')}"
            )

        articles = [
            NewsArticle(
                title=str(item.get("title") or "").strip(),
                url=str(item.get("url") or ""),
                source=(item.get("source") or {}).get("name"),
                published_at=_parse_time(item.get("publishedAt")),
            )
            for item in payload.get("articles") or []
            if item.get("title") and item.get("url")
        ]
        total = payload.get("totalResults")
        return NewsResults(
            source=self.name,
            total=int(total) if isinstance(total, int) else len(articles),
            articles=articles,
        )


class DuckDuckGoNewsProvider:
    name = "duckduckgo_news"

    def __init__(
        self,
        *,
        region: str,
        timeout: int = 15,
        retries: int = 1,
        retry_wait: float = 5.0,
        client_factory: Callable[[], Any] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._region = region
        self._retries = retries
        self._retry_wait = retry_wait
        self._client_factory = client_factory or (lambda: _ddgs(timeout))
        self._sleep = sleep
        self._lock = asyncio.Lock()

    async def search(
        self, terms: list[str], *, since: date, until: date, language: str
    ) -> NewsResults:
        query = " ".join(terms)
        # "m" (past month) covers both 7-day windows; filtered to them below.
        raw = await _call_ddgs(
            self._lock,
            lambda client: client.news(
                query, region=self._region, timelimit="m", max_results=DDG_NEWS_MAX_RESULTS
            ),
            factory=self._client_factory,
            retries=self._retries,
            retry_wait=self._retry_wait,
            sleep=self._sleep,
            what=f"news search for {query!r}",
        )
        articles: list[NewsArticle] = []
        for item in raw or []:
            published = _parse_time(item.get("date"))
            if published is not None and not since <= published.date() <= until:
                continue
            title, url = str(item.get("title") or "").strip(), str(item.get("url") or "")
            if title and url:
                articles.append(
                    NewsArticle(
                        title=title, url=url, source=item.get("source"), published_at=published
                    )
                )
        articles.sort(
            key=lambda a: a.published_at or datetime.min.replace(tzinfo=UTC), reverse=True
        )
        # DuckDuckGo reports no total: what came back is all that is known, and
        # a full page means there may be more.
        return NewsResults(source=self.name, total=len(articles), articles=articles)


class FallbackNewsProvider:
    """Ask ``primary``; if it cannot answer, ask ``fallback``.

    The result carries the name of whichever answered, so the signal says
    where its numbers came from.
    """

    def __init__(self, primary: NewsProvider, fallback: NewsProvider) -> None:
        self._primary = primary
        self._fallback = fallback
        self.name = f"{primary.name}|{fallback.name}"
        self._primary_down = False

    async def search(
        self, terms: list[str], *, since: date, until: date, language: str
    ) -> NewsResults:
        if not self._primary_down:
            try:
                return await self._primary.search(
                    terms, since=since, until=until, language=language
                )
            except ProviderUnavailable as exc:
                # Once per run: a key out of quota stays out of quota, and asking
                # it again for each of fifty candidates only burns time.
                logger.warning(
                    "%s unavailable, using %s: %s", self._primary.name, self._fallback.name, exc
                )
                self._primary_down = True
        return await self._fallback.search(terms, since=since, until=until, language=language)


def news_api_query(terms: list[str]) -> str:
    """``["vitamin d", "sleep"]`` -> ``"vitamin d" AND sleep``.

    Every term must appear; a multi-word term is a phrase, so "vitamin d" is
    not any article with the letter d in it.
    """
    parts = []
    for term in terms:
        term = term.replace('"', "").strip()
        if term:
            parts.append(f'"{term}"' if " " in term else term)
    return " AND ".join(parts)


def _parse_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
