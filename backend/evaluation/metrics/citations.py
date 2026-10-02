"""Citation metrics: does a citation exist, name a real paper, and say what its sentence says?

Five numbers, each answering a different question — they fail independently:

* ``citation_existence``: does an article that should cite anything cite at all?
* ``citation_precision``: of the sources it cites, how many are *valid* (offered
  in the prompt, resolvable, verified to exist in a registry) and *on topic*
  (the case's relevance rule)? A real paper about the wrong thing is imprecise.
* ``citation_correctness``: does the cited source actually support the sentence
  citing it? Judge verdicts per (statement, source): supported 1, partially 0.5,
  otherwise 0. This is the number that catches a citation that exists but does
  not back its claim.
* ``citation_completeness``: of the sentences reporting a finding in the evidence
  beat and the sections, how many carry a citation?
* ``citation_recall``: of the relevant sources the model was handed, how many did
  it use? Low recall with high precision is a careful but lazy article.

``citation_quality`` adds the credibility tier of what was cited
(``evaluators/credibility.py``).
"""

from __future__ import annotations

from typing import Any

from evaluation.evaluators.credibility import scholarly_tier, tier_score
from evaluation.evaluators.text import Statement

SUPPORT_SCORE = {
    "supported": 1.0,
    "partially_supported": 0.5,
    "not_supported": 0.0,
    "contradicted": 0.0,
}


def completeness(statements: list[Statement]) -> tuple[int, int]:
    """(cited findings, findings) over the evidential blocks."""
    findings = [s for s in statements if s.evidential and s.is_finding]
    return sum(1 for s in findings if s.handles), len(findings)


def cited_sources(
    statements: list[Statement], payload: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """handle -> prompt source, for every handle the body cites."""
    by_handle = {source["handle"]: source for source in payload.get("sources") or []}
    cited = {handle for s in statements for handle in s.handles}
    return {handle: by_handle[handle] for handle in sorted(cited) if handle in by_handle}


def invented_handles(statements: list[Statement], payload: dict[str, Any]) -> list[str]:
    offered = {source["handle"] for source in payload.get("sources") or []}
    return sorted({h for s in statements for h in s.handles} - offered)


def quality(sources: list[dict[str, Any]], papers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    tiers = []
    for source in sources:
        paper = papers.get(source["source_id"], {})
        tiers.append(
            scholarly_tier(
                str(source.get("study_type")),
                raw_study_type=paper.get("raw_study_type"),
                title=source.get("title") or "",
                doi=paper.get("doi"),
                journal=source.get("journal"),
            )
        )
    return {
        "tiers": tiers,
        "score": sum(tier_score(t) for t in tiers) / len(tiers) if tiers else None,
    }
