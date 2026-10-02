"""Injected failures, applied at the HTTP seam every provider already goes through.

Faults are raised *below* the production code, not around it: a 503 here
reaches ``ThrottledClient``, then the provider's own status handling, then
``RetrieveStage``'s failure accounting — the same path a real outage takes. The
only thing simulated is the network's answer.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

import httpx

from evaluation.harness.trace import SEARCH_TOOLS
from evaluation.schema import FaultSpec

#: Components that are faulted by their eval wrapper rather than over HTTP.
COMPONENT_TARGETS = frozenset({"llm_extraction", "llm_synthesis", "embeddings", "vector_store"})


class FaultInjector:
    def __init__(self, specs: list[FaultSpec]) -> None:
        self._specs = specs
        self._seen: Counter[int] = Counter()
        self._fired: Counter[int] = Counter()

    def __bool__(self) -> bool:
        return bool(self._specs)

    def match(
        self, tool: str, operation: str, params: Mapping[str, Any] | None = None
    ) -> FaultSpec | None:
        """The fault for this call, if any. Counts the call either way."""
        for index, spec in enumerate(self._specs):
            if spec.target in COMPONENT_TARGETS:
                continue
            if not _targets(spec.target, tool):
                continue
            if spec.operation is not None and spec.operation != operation:
                continue
            if spec.param_contains is not None and spec.param_contains not in json.dumps(
                dict(params or {}), default=str
            ):
                continue
            self._seen[index] += 1
            if self._seen[index] <= spec.after:
                continue
            if spec.times is not None and self._fired[index] >= spec.times:
                continue
            if spec.kind == "invalid_xml" and operation != "efetch":
                continue
            self._fired[index] += 1
            return spec
        return None

    def component(self, target: str) -> FaultSpec | None:
        return next((spec for spec in self._specs if spec.target == target), None)

    def fired(self) -> dict[str, int]:
        return {
            f"{self._specs[index].target}:{self._specs[index].kind}": count
            for index, count in self._fired.items()
        }


def _targets(target: str, tool: str) -> bool:
    return target == tool or (target == "all_search" and tool in SEARCH_TOOLS)


def fault_response(
    spec: FaultSpec, url: str, params: dict[str, Any] | None, tool: str, operation: str
) -> httpx.Response:
    """The response a faulted call receives, or the transport error it raises."""
    request = httpx.Request("GET", url, params=params)
    match spec.kind:
        case "timeout":
            raise httpx.ReadTimeout("simulated read timeout (eval fault)", request=request)
        case "connect_error":
            raise httpx.ConnectError(
                "simulated connection refused (eval fault)", request=request
            )
        case "status":
            return httpx.Response(
                spec.status or 503, text="Service Unavailable (eval fault)", request=request
            )
        case "rate_limit":
            return httpx.Response(
                429, headers={"retry-after": "0"}, text="Too Many Requests", request=request
            )
        case "malformed_json":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                text="<html><body>upstream proxy error</body></html>",
                request=request,
            )
        case "invalid_xml":
            return httpx.Response(
                200,
                text="<?xml version='1.0'?><PubmedArticleSet><PubmedArticle><MedlineCitation>",
                request=request,
            )
        case "body_throttle":
            return httpx.Response(
                200,
                text='{"error":"API rate limit exceeded","api-key":"","count":"3"}',
                request=request,
            )
        case "empty":
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                text=json.dumps(_empty_body(tool, operation)),
                request=request,
            )
    raise ValueError(f"fault kind {spec.kind!r} does not apply to HTTP calls")


def _empty_body(tool: str, operation: str) -> dict[str, Any]:
    """A well-formed answer that found nothing, in each API's own shape."""
    if tool == "pubmed":
        return {"esearchresult": {"count": "0", "retmax": "0", "idlist": []}}
    if tool in {"europe_pmc", "europe_pmc_lookup"}:
        return {"hitCount": 0, "resultList": {"result": []}}
    if tool == "openalex":
        return {"meta": {"count": 0}, "results": []}
    if tool == "semantic_scholar":
        return {"total": 0, "data": []}
    return {}
