"""Stands in for the shared ``httpx.AsyncClient`` behind every provider.

``retrieval/factory.py`` wraps one shared client in a ``ThrottledClient`` per
provider, and every provider then calls ``.get(url, params=, headers=)`` and
nothing else (``retrieval/throttle.py::HttpClient``). Implementing that one
method is therefore enough to sit beneath all of them, *unchanged*: the
production factories build the providers, the production throttle paces and
retries, and this object only decides where an answer comes from — the
network, the cassette, or an injected fault — and writes the call down.

Because it sits below ``ThrottledClient``, each retry of a throttled request is
its own recorded call. That is what lets the failure suite check that retries
are bounded rather than take the throttle's word for it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import httpx

from evaluation.harness.cassette import Cassette, stable_key
from evaluation.harness.faults import FaultInjector, fault_response
from evaluation.harness.trace import TraceRecorder

#: Never written to a trace or a cassette key.
SECRET_PARAMS = frozenset({"api_key", "apikey", "mailto", "email", "key"})

#: Header kept on a recorded response; the rest are not needed to replay it.
_KEPT_HEADERS = ("content-type", "retry-after")


def classify(url: str, params: Mapping[str, Any] | None) -> tuple[str, str]:
    """``(tool, operation)`` for a request, as the trace and faults name them."""
    parts = urlsplit(url)
    host, path = parts.netloc, parts.path
    params = params or {}
    if "eutils.ncbi.nlm.nih.gov" in host:
        return "pubmed", path.rsplit("/", 1)[-1].removesuffix(".fcgi")
    if "ebi.ac.uk" in host and "/europepmc/" in path:
        if path.endswith("/fullTextXML"):
            return "europe_pmc_fulltext", "fulltext"
        result_type = params.get("resultType")
        if result_type == "lite":
            return "europe_pmc_lookup", "lookup"
        if result_type == "idlist":
            return "europe_pmc", "count"
        return "europe_pmc", "search"
    if "api.openalex.org" in host:
        return "openalex", "search"
    if "api.semanticscholar.org" in host:
        return "semantic_scholar", "search"
    if "api.crossref.org" in host:
        return "crossref", "works"
    if host.endswith("doi.org"):
        return "doi_registry", "handle"
    if "newsapi.org" in host:
        return "news_api", "everything"
    return host or "unknown", "get"


def sanitize(params: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        key: value for key, value in (params or {}).items() if key.lower() not in SECRET_PARAMS
    }


def result_count(tool: str, operation: str, response: httpx.Response) -> int | None:
    """How many records the answer held, read the way each API shapes it.

    Best effort: ``None`` when the body does not parse, which is itself recorded
    as the call's status by the provider that reads it.
    """
    if response.status_code >= 400:
        return None
    try:
        if tool == "pubmed" and operation == "efetch":
            return response.text.count("<PubmedArticle>")
        if tool == "europe_pmc_fulltext":
            return 1
        payload = response.json()
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    if tool == "pubmed":
        result = payload.get("esearchresult") or {}
        if operation == "esearch" and "idlist" in result:
            return len(result["idlist"])
        return None
    if tool in {"europe_pmc", "europe_pmc_lookup"}:
        if operation == "count":
            hits = payload.get("hitCount")
            return hits if isinstance(hits, int) else None
        return len((payload.get("resultList") or {}).get("result") or [])
    if tool == "openalex":
        return len(payload.get("results") or [])
    if tool == "semantic_scholar":
        return len(payload.get("data") or [])
    return None


def _cacheable(tool: str, response: httpx.Response) -> bool:
    if response.status_code not in (200, 404):
        return False
    # PubMed signals throttling in a 200 body (``retrieval/pubmed.py``).
    return not (tool == "pubmed" and "API rate limit exceeded" in response.text[:500])


class EvalHttp:
    """The one object every provider's HTTP goes through during evaluation."""

    def __init__(
        self,
        real: httpx.AsyncClient | None,
        cassette: Cassette,
        mode: str,
        recorder: TraceRecorder,
        faults: FaultInjector | None = None,
    ) -> None:
        self._real = real
        self._cassette = cassette
        self._mode = mode
        self._recorder = recorder
        self._faults = faults

    async def get(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        tool, operation = classify(url, params)
        clean = sanitize(params)
        request = {"url": url, "params": _truncate(clean)}
        started = time.monotonic()

        fault = self._faults.match(tool, operation, params) if self._faults else None
        if fault is not None:
            try:
                response = fault_response(fault, url, dict(params or {}), tool, operation)
            except httpx.HTTPError as exc:
                self._recorder.record(
                    tool=tool,
                    operation=operation,
                    request=request,
                    status="fault",
                    error=str(exc),
                    fault=fault.kind,
                    latency_ms=_ms(started),
                    live_latency_ms=_ms(started),
                )
                raise
            self._recorder.record(
                tool=tool,
                operation=operation,
                request=request,
                status="fault",
                http_status=response.status_code,
                fault=fault.kind,
                result_count=result_count(tool, operation, response),
                latency_ms=_ms(started),
                live_latency_ms=_ms(started),
            )
            return response

        key = stable_key("GET", url, clean)
        if self._mode in ("cached", "replay", "frozen"):
            hit = self._cassette.get("http", key)
            if hit is not None:
                stored, recorded_latency = hit
                response = _rebuild(stored, url, params)
                self._recorder.record(
                    tool=tool,
                    operation=operation,
                    request=request,
                    http_status=response.status_code,
                    cached=True,
                    result_count=result_count(tool, operation, response),
                    latency_ms=_ms(started),
                    live_latency_ms=recorded_latency,
                )
                return response
            if self._mode == "frozen":
                # Frozen: the recording *is* the world. A request it does not
                # hold failed when it was made (429s and outages are never
                # recorded), so it fails again — the same provider down for the
                # same case — and the case is scored rather than skipped. This
                # is what lets two versions of the code be compared on exactly
                # the same search results.
                self._recorder.record(
                    tool=tool,
                    operation=operation,
                    request=request,
                    status="error",
                    error="not recorded; frozen mode replays it as an outage",
                    fault="frozen_unrecorded",
                )
                raise httpx.ConnectError(
                    "not in the recording (frozen mode)", request=httpx.Request("GET", url)
                )
            if self._mode == "replay":
                self._recorder.record(
                    tool=tool,
                    operation=operation,
                    request=request,
                    status="cassette_miss",
                    error="not recorded; replay mode makes no live calls",
                )
                # Surfaces to the provider as a transport failure, so the run
                # continues; the scorer then skips the case rather than scoring
                # an outage that never happened.
                raise httpx.ConnectError(
                    "cassette miss (replay mode)", request=httpx.Request("GET", url)
                )

        if self._real is None:
            raise RuntimeError("live HTTP requested but no client is configured")
        try:
            response = await self._real.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            self._recorder.record(
                tool=tool,
                operation=operation,
                request=request,
                status="error",
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=_ms(started),
                live_latency_ms=_ms(started),
            )
            raise
        latency = _ms(started)
        if _cacheable(tool, response):
            self._cassette.put(
                "http",
                key,
                {
                    "status": response.status_code,
                    "headers": {
                        name: response.headers[name]
                        for name in _KEPT_HEADERS
                        if name in response.headers
                    },
                    "body": response.text,
                },
                latency,
            )
        self._recorder.record(
            tool=tool,
            operation=operation,
            request=request,
            status="ok" if response.status_code < 400 else "error",
            http_status=response.status_code,
            result_count=result_count(tool, operation, response),
            latency_ms=latency,
            live_latency_ms=latency,
        )
        return response


def _rebuild(
    stored: dict[str, Any], url: str, params: Mapping[str, Any] | None
) -> httpx.Response:
    return httpx.Response(
        int(stored["status"]),
        headers=stored.get("headers") or {},
        content=str(stored["body"]).encode(),
        request=httpx.Request("GET", url, params=dict(params or {})),
    )


def _ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 1)


def _truncate(params: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    """Long id lists (efetch, esummary) would dominate a trace; keep their head."""
    out: dict[str, Any] = {}
    for key, value in params.items():
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        out[key] = text if len(text) <= limit else f"{text[:limit]}… ({len(text)} chars)"
    return out
