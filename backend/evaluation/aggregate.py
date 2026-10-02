"""Case results in, headline metrics out — each with the number of cases it rests on.

Every metric is computed only over the cases it applies to and reports that
``n``. A metric with ``n = 0`` is ``N/A``, never a pass: "no case measured
this" and "every case passed" must not print the same. Skipped cases count
toward nothing except the skipped list.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Any

from evaluation.metrics import performance
from evaluation.schema import Behavior, Category

DESCRIPTIONS = {
    "query_understanding_accuracy": "extraction named the expected subject and outcome",
    "context_carryover_rate": "follow-ups whose output stayed on the conversation's subject",
    "tool_selection_accuracy": "expected tools called, forbidden ones not",
    "unnecessary_tool_calls_per_case": "forbidden + redundant repeated calls per case",
    "tool_call_count": "tool calls per case (HTTP + model + embedding)",
    "decision_accuracy": "graph branch rules honoured, over every applicable rule",
    "agent_loop_rate": "cases that looped, timed out, or exceeded a call bound",
    "successful_termination_rate": "cases that ended on the system's own terms",
    "retrieval_recall_at_1": "gold papers in the top 1",
    "retrieval_recall_at_3": "gold papers in the top 3",
    "retrieval_recall_at_5": "gold papers in the top 5",
    "retrieval_recall_at_10": "gold papers in the top 10",
    "rank_precision_at_5": "relevant results in the top 5 (relevance rule)",
    "rank_precision_at_10": "relevant results in the top 10 (relevance rule)",
    "rank_recall_cap_at_5": "relevant pool candidates ranked into the top 5 (capped recall)",
    "rank_recall_cap_at_10": "relevant pool candidates ranked into the top 10 (capped recall)",
    "rank_mrr": "reciprocal rank of the first relevant result",
    "rank_ndcg_at_10": "graded nDCG@10 against the candidate pool",
    "empty_retrieval_rate": "cases expecting evidence that ranked nothing",
    "duplicate_rate": "duplicate papers among ranked results",
    "irrelevant_in_top_k_per_case": "ranked results matching neither subject nor rule",
    "citation_existence_rate": "articles expected to cite that cite at least one source",
    "citation_precision": "cited sources that are valid and on topic",
    "citation_correctness": "judge support of cited statements (supported 1, partial 0.5)",
    "citation_correctness_lexical_proxy": "deterministic stand-in for correctness (no judge)",
    "citation_completeness": "finding sentences in beat 2 / sections that carry a citation",
    "citation_recall": "relevant offered sources the article used",
    "citation_quality": "mean credibility tier of cited sources",
    "unsupported_citation_rate": "judged statements whose sources do not support them",
    "source_existence_rate": "cited sources confirmed to exist (excl. unverifiable)",
    "source_title_match_rate": "cited identifiers whose registry title matches",
    "retracted_citation_rate": "cited sources found retracted",
    "behavior_accuracy": "outcome matched the expected behaviour (quality cases)",
    "verdict_accuracy": "verdict inside the case's acceptable range",
    "answer_correctness": "behaviour + verdict + no forbidden claim + judge correctness ≥ 4/5",
    "answer_relevance": "judge relevance, scaled 0-1",
    "answer_completeness": "judge completeness, scaled 0-1",
    "answer_correctness_judge": "judge correctness, scaled 0-1",
    "groundedness": "cited statements supported by their sources (judge, else lexical)",
    "hallucination_rate": "validated articles with any hallucination signal",
    "draft_hallucination_rate": "every final draft, before validation filters it",
    "misattributed_number_rate": "articles quoting a real number from the wrong cited source",
    "invented_number_rate": "articles quoting a number found in no source they were shown",
    "uncertainty_appropriate_rate": "certainty proportionate to the evidence",
    "no_answer_correct_rate": "no-answer cases that declined to invent an answer",
    "validation_pass_rate": "final drafts passing the pipeline's own validation",
    "recency_cited_fraction": "cited sources from the last N years, on 'latest' queries",
    "expected_claims_present_rate": "expected claims the judge found in the article",
    "forbidden_claim_rate": "articles asserting a forbidden claim or phrase",
    "failure_recovery_rate": "failure cases ending controlled, bounded and unfabricated",
    "fallback_success_rate": "failure cases whose fallback tool still answered",
    "infinite_loop_rate": "failure cases that looped or never terminated",
    "hallucination_after_tool_failure_rate": "failure cases producing fabricated output",
    "retry_classification_accuracy": "transient failures marked retryable, permanent ones not",
    "latency_mean_s": "wall clock per executed case, as run",
    "latency_median_s": "wall clock per executed case, as run",
    "latency_p95_s": "wall clock per executed case, as run (needs ≥ 20 cases)",
    "latency_p99_s": "wall clock per executed case, as run (needs ≥ 100 cases)",
}


def _metric(group: str, value: float | None, n: int, **extra: Any) -> dict[str, Any]:
    return {"group": group, "value": value, "n": n, **extra}


def _mean(values: Iterable[float | None]) -> tuple[float | None, int]:
    present = [float(v) for v in values if v is not None]
    return (sum(present) / len(present), len(present)) if present else (None, 0)


def _rate(
    rows: list[dict[str, Any]], predicate: Callable[[dict[str, Any]], bool]
) -> tuple[float | None, int]:
    if not rows:
        return None, 0
    return sum(1 for row in rows if predicate(row)) / len(rows), len(rows)


def aggregate(results: list[dict[str, Any]], ks: list[int]) -> dict[str, dict[str, Any]]:
    executed = [r for r in results if r["status"] == "executed"]
    quality = [r for r in executed if not r["is_failure_case"]]
    failure = [r for r in executed if r["is_failure_case"]]
    pipeline = [r for r in executed if "agent" in r and "tool_selection" in r["agent"]]
    m: dict[str, dict[str, Any]] = {}

    def put(name: str, group: str, pair: tuple[float | None, int], **extra: Any) -> None:
        m[name] = _metric(group, pair[0], pair[1], **extra)

    # --- query understanding ---------------------------------------------------
    qu = [r["query_understanding"] for r in quality if r.get("query_understanding")]
    put(
        "query_understanding_accuracy",
        "query",
        _mean(float(q["correct"]) if q.get("correct") is not None else None for q in qu),
    )
    put(
        "context_carryover_rate",
        "query",
        _mean(
            float(q["context_carryover"]) if q.get("context_carryover") is not None else None
            for q in qu
        ),
    )

    # --- agent -----------------------------------------------------------------
    put(
        "tool_selection_accuracy",
        "agent",
        _mean(float(r["agent"]["tool_selection"]["correct"]) for r in pipeline),
    )
    put(
        "unnecessary_tool_calls_per_case",
        "agent",
        _mean(r["agent"]["tool_selection"]["unnecessary_calls"] for r in pipeline),
    )
    put("tool_call_count", "agent", _mean(r["agent"]["stats"]["tool_calls"] for r in pipeline))
    rules = [rule for r in pipeline for rule in r["agent"]["decisions"]]
    put(
        "decision_accuracy",
        "agent",
        (
            (sum(1 for rule in rules if rule["passed"]) / len(rules), len(rules))
            if rules
            else (None, 0)
        ),
        unit="rules",
    )
    with_agent = [r for r in executed if r.get("agent")]
    put("agent_loop_rate", "agent", _rate(with_agent, lambda r: r["agent"]["loop_detected"]))
    put(
        "successful_termination_rate",
        "agent",
        _rate(with_agent, lambda r: r["agent"]["terminated"]),
    )

    # --- retrieval -------------------------------------------------------------
    ret = [r["retrieval"] for r in quality if (r.get("retrieval") or {}).get("applicable")]
    for k in ks:
        put(
            f"retrieval_recall_at_{k}",
            "retrieval",
            _mean(x.get(f"gold_recall@{k}") for x in ret),
        )
    for k in (5, 10):
        put(f"rank_precision_at_{k}", "retrieval", _mean(x.get(f"precision@{k}") for x in ret))
        put(
            f"rank_recall_cap_at_{k}", "retrieval", _mean(x.get(f"recall_cap@{k}") for x in ret)
        )
    put("rank_ndcg_at_10", "retrieval", _mean(x.get("ndcg@10") for x in ret))
    put("rank_mrr", "retrieval", _mean(x.get("mrr") for x in ret))
    evidence = [
        r
        for r in quality
        if (r.get("retrieval") or {}).get("applicable")
        and r["expected_behavior"]
        in {Behavior.ANSWER_WITH_CITATIONS, Behavior.ACKNOWLEDGE_UNCERTAINTY}
    ]
    put("empty_retrieval_rate", "retrieval", _rate(evidence, lambda r: r["retrieval"]["empty"]))
    ranked_total = sum(x["ranked_total"] for x in ret)
    put(
        "duplicate_rate",
        "retrieval",
        (
            (sum(x["duplicates"] for x in ret) / ranked_total, len(ret))
            if ranked_total
            else (None, 0)
        ),
    )
    put(
        "irrelevant_in_top_k_per_case",
        "retrieval",
        _mean(x.get("irrelevant_in_top") for x in ret),
    )

    # --- citations -------------------------------------------------------------
    articles = [r for r in quality if r.get("answer")]
    cit = [r["citations"] for r in articles]
    expects_cites = [
        r
        for r in articles
        if r["expected_behavior"]
        in {Behavior.ANSWER_WITH_CITATIONS, Behavior.ACKNOWLEDGE_UNCERTAINTY}
    ]
    put(
        "citation_existence_rate",
        "citations",
        _rate(expects_cites, lambda r: r["citations"]["count"] > 0),
    )
    cited_total = sum(c["count"] for c in cit)
    put(
        "citation_precision",
        "citations",
        (
            (sum(c["precise"] for c in cit) / cited_total, len([c for c in cit if c["count"]]))
            if cited_total
            else (None, 0)
        ),
        unit="citations",
    )
    judged = [s for c in cit for s in c["statements"] if s.get("judge")]
    support = {"supported": 1.0, "partially_supported": 0.5}
    put(
        "citation_correctness",
        "citations",
        (
            (sum(support.get(s["judge"], 0.0) for s in judged) / len(judged), len(judged))
            if judged
            else (None, 0)
        ),
        unit="statements",
    )
    put(
        "unsupported_citation_rate",
        "citations",
        (
            (
                sum(1 for s in judged if s["judge"] in {"not_supported", "contradicted"})
                / len(judged),
                len(judged),
            )
            if judged
            else (None, 0)
        ),
        unit="statements",
    )
    put(
        "citation_correctness_lexical_proxy",
        "citations",
        _mean(c["lexical_support_proxy"] for c in cit),
    )
    # The label mix behind citation_correctness, so a strict judge's
    # "partially_supported" reads differently from "not_supported".
    m["citation_correctness"]["distribution"] = dict(Counter(s["judge"] for s in judged))
    findings = sum(c["findings"] for c in cit)
    put(
        "citation_completeness",
        "citations",
        (
            (sum(c["cited_findings"] for c in cit) / findings, findings)
            if findings
            else (None, 0)
        ),
        unit="sentences",
    )
    put("citation_recall", "citations", _mean(c["recall"] for c in cit))
    put("citation_quality", "citations", _mean(c["quality"] for c in cit))

    # --- sources ---------------------------------------------------------------
    source_rows = [
        row for r in articles for row in r["citations"]["cited"] if not row.get("injected")
    ]
    decided = [
        row for row in source_rows if row["registry"] not in {"unverifiable", "unchecked"}
    ]
    put(
        "source_existence_rate",
        "sources",
        (
            (
                sum(1 for row in decided if row["registry"] not in {"nonexistent", "malformed"})
                / len(decided),
                len(decided),
            )
            if decided
            else (None, 0)
        ),
        unit="citations",
        unverifiable=len(source_rows) - len(decided),
    )
    put(
        "source_title_match_rate",
        "sources",
        (
            (
                sum(1 for row in decided if row["registry"] != "title_mismatch") / len(decided),
                len(decided),
            )
            if decided
            else (None, 0)
        ),
        unit="citations",
    )
    put(
        "retracted_citation_rate",
        "sources",
        (
            (
                sum(1 for row in decided if row["registry"] == "retracted") / len(decided),
                len(decided),
            )
            if decided
            else (None, 0)
        ),
        unit="citations",
    )

    # --- answers ---------------------------------------------------------------
    put("behavior_accuracy", "answers", _rate(quality, lambda r: bool(r.get("behavior_ok"))))
    ans = [r["answer"] for r in articles]
    put(
        "verdict_accuracy",
        "answers",
        _mean(float(a["verdict_ok"]) if a["verdict_ok"] is not None else None for a in ans),
    )
    put("answer_correctness", "answers", _mean(float(a["answer_correct"]) for a in ans))
    judges = [a["judge"] for a in ans if a.get("judge")]
    put("answer_relevance", "answers", _mean((j["relevance"] - 1) / 4 for j in judges))
    put("answer_completeness", "answers", _mean((j["completeness"] - 1) / 4 for j in judges))
    put(
        "answer_correctness_judge", "answers", _mean((j["correctness"] - 1) / 4 for j in judges)
    )
    grounded = [a["groundedness"] for a in ans if a.get("groundedness") is not None]
    put("groundedness", "answers", _mean(grounded))
    validated = [a for a in ans if a["validated"]]
    put("hallucination_rate", "answers", _rate(validated, lambda a: a["hallucination"]))
    put("draft_hallucination_rate", "answers", _rate(ans, lambda a: a["hallucination"]))
    put(
        "misattributed_number_rate",
        "answers",
        _rate(ans, lambda a: any("(misattributed)" in r for r in a["hallucination_reasons"])),
    )
    put(
        "invented_number_rate",
        "answers",
        _rate(ans, lambda a: any("(invented)" in r for r in a["hallucination_reasons"])),
    )
    put(
        "uncertainty_appropriate_rate",
        "answers",
        _mean(
            float(a["uncertainty_appropriate"])
            if a.get("uncertainty_appropriate") is not None
            else None
            for a in ans
        ),
    )
    no_answer = [r for r in quality if r["category"] == Category.NO_ANSWER]
    put(
        "no_answer_correct_rate",
        "answers",
        _rate(no_answer, lambda r: bool(r.get("behavior_ok"))),
    )
    put("validation_pass_rate", "answers", _rate(ans, lambda a: a["validated"]))
    put("recency_cited_fraction", "answers", _mean(a.get("recent_cited_fraction") for a in ans))
    claims = [c for a in ans for c in a["expected_claims"] if c["status"] is not None]
    put(
        "expected_claims_present_rate",
        "answers",
        _rate(claims, lambda c: c["status"] == "present"),
    )
    put(
        "forbidden_claim_rate",
        "answers",
        _rate(
            [
                r["answer"]
                for r in articles
                if r["answer"]["forbidden_claims"] or r["answer"]["forbidden_phrases_found"]
            ],
            lambda a: (
                bool(a["forbidden_phrases_found"])
                or any(c["asserted"] for c in a["forbidden_claims"])
            ),
        ),
    )

    # --- failure handling --------------------------------------------------------
    fail = [r["failure"] for r in failure if r.get("failure")]
    put("failure_recovery_rate", "failure", _rate(fail, lambda f: f["recovered"]))
    with_fallback = [f for f in fail if f.get("fallback_ok") is not None]
    put("fallback_success_rate", "failure", _rate(with_fallback, lambda f: f["fallback_ok"]))
    put("infinite_loop_rate", "failure", _rate(fail, lambda f: f["loop"]))
    put(
        "hallucination_after_tool_failure_rate",
        "failure",
        _rate(fail, lambda f: f["hallucinated_after_failure"]),
    )
    classified = [f for f in fail if f.get("retry_classification_ok") is not None]
    put(
        "retry_classification_accuracy",
        "failure",
        _rate(classified, lambda f: f["retry_classification_ok"]),
    )

    # --- performance -------------------------------------------------------------
    walls = [r["performance"]["wall_clock_s"] for r in executed if r.get("performance")]
    stats = performance.summary(walls)
    for key in ("mean", "median", "p95", "p99"):
        put(f"latency_{key}_s", "performance", (stats.get(key), stats["n"]))

    for name, metric in m.items():
        metric["description"] = DESCRIPTIONS.get(name, "")
    return m


def performance_breakdown(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-tool live latency, per-stage time, calls, tokens, cost."""
    executed = [r for r in results if r["status"] == "executed" and r.get("performance")]
    by_tool: dict[str, list[float]] = {}
    by_stage: dict[str, list[float]] = {}
    totals: Counter[str] = Counter()
    costs: list[float] = []
    unpriced: set[str] = set()
    for r in executed:
        perf = r["performance"]
        for tool, values in perf["live_latency_ms_by_tool"].items():
            by_tool.setdefault(tool, []).extend(values)
        for stage, seconds in perf["stage_s"].items():
            by_stage.setdefault(stage, []).append(seconds)
        totals.update(
            {
                "calls": perf["calls"],
                "live_calls": perf["live_calls"],
                "replayed_calls": perf["replayed_calls"],
                "tokens_in": perf["tokens_in"],
                "tokens_out": perf["tokens_out"],
            }
        )
        if perf["cost_priced"]:
            costs.append(perf["cost_usd"] or 0.0)
        else:
            unpriced.update(perf["models"])
    return {
        "cases": len(executed),
        "wall_clock_s": performance.summary(
            [r["performance"]["wall_clock_s"] for r in executed]
        ),
        "search_live_s": performance.summary(
            [r["performance"]["search_live_ms"] / 1000 for r in executed]
        ),
        "llm_live_s": performance.summary(
            [r["performance"]["llm_live_ms"] / 1000 for r in executed]
        ),
        "retrieval_stage_s": performance.summary(
            [r["performance"]["retrieval_s"] for r in executed]
        ),
        "live_latency_ms_by_tool": {
            t: performance.summary(v) for t, v in sorted(by_tool.items())
        },
        "stage_s": {s: performance.summary(v) for s, v in sorted(by_stage.items())},
        "totals": dict(totals),
        "per_case": {
            key: totals[key] / len(executed) if executed else None
            for key in ("calls", "live_calls", "tokens_in", "tokens_out")
        },
        "cost_usd": sum(costs) if costs and not unpriced else None,
        "unpriced_models": sorted(unpriced),
    }
