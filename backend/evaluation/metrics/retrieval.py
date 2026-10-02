"""Retrieval quality, measured on the ranked lists before any model writes a word.

This is what separates "the answer was bad because retrieval missed the
evidence" from "retrieval found it and generation ignored it" — the first
question a failing case raises.

Two kinds of ground truth, reported under different names because they answer
different questions:

* **Gold papers** (``relevant_ids`` on the case, each verified to exist).
  ``retrieval_recall@k``: did search + ranking surface the papers an expert
  would expect? End to end over the whole retrieval layer.
* **A relevance rule** (``expected_subject`` x ``expected_outcome_terms``), applied
  to every candidate the providers returned. A paper is graded 2 when its title
  or abstract names the subject *and* an outcome, 1 for the subject alone, 0
  otherwise. The rule is written per case by the dataset author, independent of
  the pipeline's own query builder. It gives precision, MRR and nDCG over the
  ranked list, and ``rank_recall_cap@k`` over the candidate pool — whether
  RANK put the relevant candidates the providers *did* find into the top k.

``rank_recall_cap@k`` divides by ``min(k, relevant in pool)`` (BEIR's capped
recall) rather than by every relevant candidate. With fifty relevant papers in
a pool, plain recall@5 cannot exceed 0.1, which would measure the pool size
rather than the ranking.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from typing import Any

from evaluation.evaluators.source_validator import title_similarity


def _norm(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


def mentions(text: str, terms: Iterable[str]) -> bool:
    """Any term at the start of a word, case-insensitive.

    A prefix rather than a whole word on purpose: dataset terms are often stems
    ("depress", "injur", "cognit") so that one term covers every form.
    """
    haystack = f" {_norm(text)} "
    for term in terms:
        needle = _norm(term)
        if needle and f" {needle}" in haystack:
            return True
    return False


def relevance_grade(paper: dict[str, Any], subject: list[str], outcome: list[str]) -> int:
    text = f"{paper.get('title', '')} {paper.get('abstract', '')}"
    if not subject or not mentions(text, subject):
        return 0
    if not outcome or mentions(text, outcome):
        return 2
    return 1


def identity_keys(paper: dict[str, Any]) -> set[str]:
    keys = set()
    if paper.get("pmid"):
        keys.add(f"pmid:{str(paper['pmid']).strip()}")
    if paper.get("doi"):
        keys.add(f"doi:{str(paper['doi']).strip().lower()}")
    return keys


def recall_at_k(ranked: list[set[str]], gold: set[str], k: int) -> float | None:
    """Gold identifiers found in the top k, over all gold. ``ranked`` holds each
    result's identity keys, so a paper matches by PMID or by DOI."""
    if not gold:
        return None
    found = set().union(*ranked[:k]) if ranked[:k] else set()
    return len(gold & found) / len(gold)


def precision_at_k(grades: list[int], k: int) -> float | None:
    """Relevant (grade 2) results in the first ``min(k, n)``.

    Truncated to the list length so a short, all-relevant list for a thinly
    studied topic scores 1.0 rather than being penalised for the literature's
    size.
    """
    head = grades[:k]
    if not head:
        return None
    return sum(1 for grade in head if grade >= 2) / len(head)


def recall_cap_at_k(grades: list[int], pool_relevant: int, k: int) -> float | None:
    if pool_relevant == 0:
        return None
    return sum(1 for grade in grades[:k] if grade >= 2) / min(k, pool_relevant)


def reciprocal_rank(grades: list[int]) -> float | None:
    if not grades:
        return None
    for position, grade in enumerate(grades, start=1):
        if grade >= 2:
            return 1.0 / position
    return 0.0


def ndcg_at_k(grades: list[int], pool_grades: list[int], k: int) -> float | None:
    """Graded nDCG, the ideal ordering drawn from the whole candidate pool."""

    def dcg(values: list[int]) -> float:
        return sum((2**grade - 1) / math.log2(i + 2) for i, grade in enumerate(values[:k]))

    ideal = dcg(sorted(pool_grades, reverse=True))
    if ideal == 0:
        return None
    return dcg(grades) / ideal


def duplicate_count(papers: list[dict[str, Any]]) -> int:
    """Pairs in one ranked list that are the same paper: a shared identifier, or
    near-identical titles. Each is a dedup failure the model may cite twice."""
    duplicates = 0
    seen_keys: set[str] = set()
    titles: list[str] = []
    for paper in papers:
        keys = identity_keys(paper)
        title = paper.get("title") or ""
        if keys & seen_keys or any(title_similarity(title, other) >= 0.95 for other in titles):
            duplicates += 1
        seen_keys |= keys
        titles.append(title)
    return duplicates


def evaluate(
    trace: dict[str, Any],
    *,
    subject: list[str],
    outcome: list[str],
    gold: set[str],
    ks: list[int],
    recent_from: int,
) -> dict[str, Any]:
    """Per-case retrieval metrics over the final round, averaged across claims."""
    rounds = trace.get("rounds") or []
    papers: dict[str, dict[str, Any]] = trace.get("papers") or {}
    if not rounds:
        return {"applicable": False}
    final = rounds[-1]
    per_claim: list[dict[str, Any]] = []
    listed: list[dict[str, Any]] = []

    for claim, entries in final["ranked"].items():
        ranked = [
            papers[entry["source_id"]] for entry in entries if entry["source_id"] in papers
        ]
        pool = [papers[i] for i in final["candidates"].get(claim, []) if i in papers]
        grades = [relevance_grade(paper, subject, outcome) for paper in ranked]
        pool_grades = [relevance_grade(paper, subject, outcome) for paper in pool]
        pool_relevant = sum(1 for grade in pool_grades if grade >= 2)
        # With nothing relevant anywhere in the pool — a no-answer topic — the
        # ranking had nothing to put first, so precision and MRR are undefined
        # rather than zero. What happens to the junk is measured downstream,
        # by citation precision.
        graded = bool(subject) and pool_relevant > 0
        metrics: dict[str, Any] = {
            "claim": claim,
            "ranked": len(ranked),
            "pool": len(pool),
            "pool_relevant": pool_relevant,
            "mrr": reciprocal_rank(grades) if graded else None,
            "duplicates": duplicate_count(ranked),
        }
        for k in ks:
            metrics[f"precision@{k}"] = precision_at_k(grades, k) if graded else None
            metrics[f"recall_cap@{k}"] = (
                recall_cap_at_k(grades, pool_relevant, k) if subject else None
            )
            metrics[f"ndcg@{k}"] = ndcg_at_k(grades, pool_grades, k) if subject else None
        per_claim.append(metrics)
        for paper, grade, entry in zip(ranked, grades, entries, strict=False):
            listed.append(
                {
                    "claim": claim,
                    "handle": entry.get("handle"),
                    "source_id": paper["source_id"],
                    "pmid": paper.get("pmid"),
                    "doi": paper.get("doi"),
                    "title": paper.get("title"),
                    "year": paper.get("year"),
                    "study_type": paper.get("study_type"),
                    "score": entry.get("score"),
                    "relevance": grade if subject else None,
                    "gold": bool(identity_keys(paper) & gold),
                }
            )

    def mean(key: str) -> float | None:
        values = [m[key] for m in per_claim if m.get(key) is not None]
        return sum(values) / len(values) if values else None

    # Gold recall reads the claims' lists rank by rank: ``by_rank[i]`` is every
    # paper at position i of some claim's list, so recall@k means "in the first
    # k of at least one claim's ranking".
    lists = [
        [identity_keys(papers[e["source_id"]]) for e in entries if e["source_id"] in papers]
        for entries in final["ranked"].values()
    ]
    depth = max((len(items) for items in lists), default=0)
    by_rank = [
        set().union(*(items[rank] for items in lists if rank < len(items)))
        for rank in range(depth)
    ]

    total_ranked = sum(m["ranked"] for m in per_claim)
    years = [p.get("year") for p in listed if p.get("year")]
    result: dict[str, Any] = {
        "applicable": True,
        "claims": len(per_claim),
        "rounds": len(rounds),
        "empty": total_ranked == 0,
        "ranked_total": total_ranked,
        "pool_total": sum(m["pool"] for m in per_claim),
        "duplicates": sum(m["duplicates"] for m in per_claim),
        "duplicate_rate": (
            sum(m["duplicates"] for m in per_claim) / total_ranked if total_ranked else None
        ),
        "mrr": mean("mrr"),
        "recent_fraction": (
            sum(1 for year in years if year >= recent_from) / len(years) if years else None
        ),
        "irrelevant_in_top": sum(1 for p in listed if p["relevance"] == 0) if subject else None,
        "per_claim": per_claim,
        "ranked": listed,
    }
    for k in ks:
        result[f"precision@{k}"] = mean(f"precision@{k}")
        result[f"recall_cap@{k}"] = mean(f"recall_cap@{k}")
        result[f"ndcg@{k}"] = mean(f"ndcg@{k}")
        result[f"gold_recall@{k}"] = recall_at_k(by_rank, gold, k)
    return result
