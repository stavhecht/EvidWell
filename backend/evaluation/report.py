"""Turn a scored run into the console summary, ``report.md`` and per-case debug blocks.

Status per metric:

* **PASS** — meets its threshold, outside the warn margin.
* **WARN** — meets it within ``warn_margin``, or misses a *non-critical* one.
* **FAIL** — misses a *critical* threshold. Any FAIL makes the run exit 1.
* **N/A** — no case measured it (``n = 0``). Never counted as a pass.
* **INFO** — reported, no threshold set.
"""

from __future__ import annotations

from typing import Any

from evaluation.config import Threshold

#: Metrics shown as plain numbers rather than percentages.
_PLAIN = ("tool_call_count", "unnecessary_tool_calls_per_case", "irrelevant_in_top_k_per_case")

#: Order of groups in reports.
GROUP_ORDER = [
    "query",
    "agent",
    "retrieval",
    "citations",
    "sources",
    "answers",
    "failure",
    "performance",
]
GROUP_TITLES = {
    "query": "Query understanding",
    "agent": "Agent / tool selection",
    "retrieval": "Retrieval",
    "citations": "Citations",
    "sources": "Source validation",
    "answers": "Answers",
    "failure": "Failure handling",
    "performance": "Performance",
}


def fmt(name: str, value: float | None) -> str:
    if value is None:
        return "—"
    if name.startswith("latency"):
        return f"{value:.1f}s"
    if name in _PLAIN:
        return f"{value:.2f}"
    return f"{value * 100:.1f}%"


def status(name: str, metric: dict[str, Any], thresholds: dict[str, Threshold]) -> str:
    threshold = thresholds.get(name)
    if metric["value"] is None or metric["n"] == 0:
        return "N/A"
    if threshold is None:
        return "INFO"
    value, target = metric["value"], threshold.value
    if threshold.direction == "min":
        meets = value >= target
        margin = value - target
    else:
        meets = value <= target
        margin = target - value
    if not meets:
        return "FAIL" if threshold.critical else "WARN"
    if name.startswith("latency") or name in _PLAIN:
        # Unbounded scales: the margin is relative to the threshold.
        close = target and margin / abs(target) < threshold.warn_margin
    else:
        # Rates live in [0, 1]. A perfect score cannot be "close to failing",
        # however tight the threshold.
        perfect = value >= 1.0 if threshold.direction == "min" else value <= 0.0
        close = not perfect and margin < threshold.warn_margin
    return "WARN" if close else "PASS"


def threshold_text(name: str, thresholds: dict[str, Threshold]) -> str:
    threshold = thresholds.get(name)
    if threshold is None:
        return ""
    op = "≥" if threshold.direction == "min" else "≤"
    crit = " (critical)" if threshold.critical else ""
    return f"{op} {fmt(name, threshold.value)}{crit}"


def console(results: dict[str, Any], thresholds: dict[str, Threshold]) -> str:
    counts = results["counts"]
    lines = [
        "",
        "Evaluation Results",
        "==================",
        f"suite {results['suite']} · mode {results['mode']} · {counts['executed']} executed, "
        f"{counts['skipped']} skipped, {counts['passed']} cases fully passed",
        f"generator {results['environment'].get('synthesis_model')} · judge "
        f"{results['judge'].get('model') or 'disabled'}"
        + (f" ({results['judge']['reason']})" if results["judge"].get("reason") else ""),
        "",
    ]
    for group in GROUP_ORDER:
        names = [n for n, m in results["metrics"].items() if m["group"] == group]
        if not names:
            continue
        lines.append(GROUP_TITLES[group])
        for name in names:
            metric = results["metrics"][name]
            state = status(name, metric, thresholds)
            if state == "N/A" and name not in thresholds:
                continue
            lines.append(
                f"  {name:<40} {fmt(name, metric['value']):>8}  {state:<4}  "
                f"n={metric['n']:<4} {threshold_text(name, thresholds)}"
            )
        lines.append("")
    regression = results.get("regression")
    if regression and regression.get("rows"):
        lines.append(f"Regression vs baseline ({regression['baseline_created_at']}):")
        moved = [row for row in regression["rows"] if abs(row["delta"]) > 1e-9]
        if not moved:
            lines.append("  no metric moved")
        for row in sorted(moved, key=lambda r: (not r["regression"], r["metric"]))[:30]:
            sign = "+" if row["delta"] >= 0 else ""
            delta = (
                f"{sign}{row['delta']:.1f}s"
                if row["metric"].startswith("latency")
                else f"{sign}{row['delta'] * 100:.1f}%"
            )
            flag = "  REGRESSION" if row["regression"] else ""
            lines.append(f"  {row['metric']:<40} {delta:>8}{flag}")
        changes = regression.get("cases") or {}
        if changes.get("newly_failing"):
            lines.append(f"  newly failing: {', '.join(changes['newly_failing'])}")
        if changes.get("newly_passing"):
            lines.append(f"  newly passing: {', '.join(changes['newly_passing'])}")
        lines.append("")
    elif regression is not None:
        lines.append(f"Regression: {regression.get('note')}")
        lines.append("")
    lines.append(f"Overall: {results['verdict']}")
    lines.append(f"Report:  {results['report_path']}")
    return "\n".join(lines)


def _checks_table(checks: list[dict[str, Any]]) -> list[str]:
    lines = []
    for check in checks:
        state = "n/a" if check["passed"] is None else "PASS" if check["passed"] else "FAIL"
        lines.append(f"- **{check['name']}** ({check['group']}): {state} — {check['detail']}")
    return lines


def case_block(result: dict[str, Any], trace: dict[str, Any] | None) -> str:
    """The per-test debug view: query, tools, documents, answer, citations, checks."""
    lines = [f"### {result['id']} — {'PASS' if result['passed'] else 'FAIL'}", ""]
    lines.append(f"**Query** ({result['category']}):")
    lines.append("")
    lines.extend(f"> {line}" for line in result["query"].splitlines())
    lines.append("")
    lines.append(
        f"**Outcome:** `{result['outcome']}` "
        f"(expected behaviour `{result['expected_behavior']}`; "
        f"acceptable {', '.join(f'`{o}`' for o in result['acceptable_outcomes'])})"
    )
    if result.get("error"):
        error = result["error"]
        lines.append(f"**Error:** {error.get('type')}: {str(error.get('message'))[:400]}")
    qu = result.get("query_understanding") or {}
    if qu.get("extracted"):
        lines.append(
            f"**Understood as:** product `{qu['product']}`, ingredients {qu['ingredients']}, "
            f"claims {qu['claims']}"
        )
    agent_info = result.get("agent") or {}
    selection = agent_info.get("tool_selection")
    if selection:
        stats = agent_info["stats"]
        lines.append(
            f"**Selected tools:** {', '.join(selection['called']) or 'none'} "
            f"({stats['tool_calls']} calls: {stats['http_calls']} HTTP, "
            f"{stats['llm_calls']} LLM, "
            f"{stats['embedding_calls']} embedding; {stats['retries']} retries, "
            f"{stats['failed_calls']} failed)"
        )
    decisions = agent_info.get("trace_decisions") or []
    if decisions:
        lines.append(
            "**Agent decisions:** "
            + " → ".join(f"{d['after']}: {d['decision']}" for d in decisions)
        )
    retrieval_info = result.get("retrieval") or {}
    if retrieval_info.get("applicable"):
        lines.append("")
        lines.append(
            "**Retrieved documents** "
            f"(final round, {retrieval_info['ranked_total']} ranked from "
            f"{retrieval_info['pool_total']} candidates):"
        )
        lines.append("")
        labels = {2: "relevant", 1: "subject only", 0: "irrelevant", None: "unlabelled"}
        for row in (retrieval_info.get("ranked") or [])[:12]:
            ident = f"PMID {row['pmid']}" if row.get("pmid") else f"DOI {row.get('doi')}"
            gold = " ★gold" if row.get("gold") else ""
            lines.append(
                f"{row['handle']}. {ident} — {labels[row['relevance']]}{gold} — "
                f"{row['study_type']}, {row.get('year')} — {str(row['title'])[:110]} "
                f"(score {row['score']})"
            )
    answer = result.get("answer")
    if answer:
        lines.append("")
        validation = (
            "passed" if answer["validated"] else f"FAILED {answer['validation_failures']}"
        )
        lines.append(
            f"**Generated answer** — verdict `{answer['verdict']}` (ceiling "
            f"`{answer.get('verdict_ceiling')}`), validation {validation}:"
        )
        lines.append("")
        lines.append(f"> **{answer['headline']}** — {answer['summary']}")
        lines.append(">")
        for paragraph in str(answer.get("body") or "").split("\n"):
            if paragraph.strip():
                lines.append(f"> {paragraph[:700]}")
        cites = result.get("citations") or {}
        lines.append("")
        lines.append("**Citations:**")
        lines.append("")
        for row in cites.get("cited") or []:
            ident = f"PMID {row['pmid']}" if row.get("pmid") else f"DOI {row.get('doi')}"
            relevance = {2: "on topic", 1: "subject only", 0: "OFF TOPIC", None: "—"}[
                row["relevance"]
            ]
            lines.append(
                f"- [{row['handle']}] {ident} · {row['study_type']}, {row.get('year')} · "
                f"registry: {row['registry']} · {relevance}"
            )
        judged = [s for s in cites.get("statements") or [] if s.get("judge")]
        if judged:
            lines.append("")
            lines.append("**Citation checks (judge):**")
            lines.append("")
            for row in judged:
                lines.append(
                    f"- `{row['judge']}` {', '.join(row['handles'])}: “{row['text'][:160]}” — "
                    f"{row['judge_explanation']} (lexical support {row['lexical_support']:.2f}"
                    + (
                        f", unsupported numbers {row['unsupported_numbers']}"
                        if row["unsupported_numbers"]
                        else ""
                    )
                    + ")"
                )
        if answer.get("judge"):
            judge = answer["judge"]
            lines.append("")
            lines.append(
                f"**Judge:** relevance {judge['relevance']}/5, completeness "
                f"{judge['completeness']}/5, correctness {judge['correctness']}/5, certainty "
                f"{judge['certainty']} — {judge['reasoning']}"
            )
    research = result.get("research")
    if research:
        lines.append("")
        ended = f"failed: {research['failed']}" if research["failed"] else "completed"
        statuses = {name: row["status"] for name, row in research["provider_status"].items()}
        lines.append(
            f"**Research run:** {ended}; provider status {statuses}; "
            f"selected {research['selected']}; faults fired {research.get('faults_fired')}"
        )
    failure = result.get("failure")
    if failure and failure.get("fallback"):
        lines.append(f"**Fallback:** {failure['fallback']}")
    lines.append("")
    lines.append("**Evaluation:**")
    lines.append("")
    lines.extend(_checks_table(result.get("checks") or []))
    failed = [c for c in result.get("checks") or [] if c["passed"] is False]
    if failed:
        lines.append("")
        lines.append(
            "**Reason:** " + " ".join(f"{c['name']}: {c['detail']}." for c in failed[:4])
        )
    if trace is not None:
        lines.append("")
        lines.append(f"Full trace: `traces/{result['id']}.json`")
    lines.append("")
    return "\n".join(lines)


def markdown(results: dict[str, Any], thresholds: dict[str, Threshold]) -> str:
    counts = results["counts"]
    env = results["environment"]
    judge = results["judge"]
    lines = [
        f"# Evaluation report — {results['suite']}",
        "",
        f"- Run: {results['started_at']} → {results['finished_at']} · "
        f"mode `{results['mode']}` · git `{results['git']}`",
        f"- Pipeline: extraction `{env.get('extraction_model')}`, synthesis "
        f"`{env.get('synthesis_model')}`, embeddings `{env.get('embedding_model')}`, providers "
        f"{env.get('enabled_providers')}, top-k {env.get('retrieval_top_k')}",
        f"- Judge: `{judge.get('model') or 'disabled'}`"
        + (f" — {judge['reason']}" if judge.get("reason") else "")
        + f" · {judge.get('calls', 0)} live / {judge.get('cached_calls', 0)} cached calls, "
        f"{judge.get('failures', 0)} failed",
        f"- Cases: **{counts['executed']} executed**, {counts['skipped']} skipped, "
        f"{counts['passed']} fully passed, {counts['failed']} with at least one failed check",
        f"- Overall: **{results['verdict']}**",
        "",
    ]
    if results.get("notes"):
        lines.append("Notes:")
        lines.append("")
        lines.extend(f"- {note}" for note in results["notes"])
        lines.append("")
    if results.get("skipped"):
        lines.append("## Skipped cases")
        lines.append("")
        lines.extend(f"- `{row['id']}` — {row['reason']}" for row in results["skipped"])
        lines.append("")

    lines.append("## Metrics")
    lines.append("")
    for group in GROUP_ORDER:
        names = [n for n, m in results["metrics"].items() if m["group"] == group]
        if not names:
            continue
        lines.append(f"### {GROUP_TITLES[group]}")
        lines.append("")
        lines.append("| metric | value | status | n | threshold | what it measures |")
        lines.append("|---|---:|---|---:|---|---|")
        for name in names:
            metric = results["metrics"][name]
            cells = [
                f"`{name}`",
                fmt(name, metric["value"]),
                status(name, metric, thresholds),
                str(metric["n"]),
                threshold_text(name, thresholds),
                metric["description"],
            ]
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")
        distribution = (results["metrics"].get("citation_correctness") or {}).get(
            "distribution"
        )
        if group == "citations" and distribution:
            lines.append(
                "Judge labels behind `citation_correctness`: "
                + ", ".join(f"{label} {count}" for label, count in sorted(distribution.items()))
            )
            lines.append("")

    perf = results.get("performance") or {}
    if perf:
        lines.append("### Latency and cost detail")
        lines.append("")
        lines.append(
            f"Wall clock is as run ({perf['totals'].get('replayed_calls', 0)} of "
            f"{perf['totals'].get('calls', 0)} calls replayed from the recording); "
            "live latency is what each call cost when actually made."
        )
        lines.append("")
        lines.append("| measure | n | mean | median | p95 | p99 |")
        lines.append("|---|---:|---:|---:|---:|---:|")

        def row(label: str, s: dict[str, Any], unit: str = "s", scale: float = 1.0) -> str:
            def v(key: str) -> str:
                value = s.get(key)
                return "—" if value is None else f"{value / scale:.1f}{unit}"

            cells = [label, str(s.get("n", 0)), v("mean"), v("median"), v("p95"), v("p99")]
            return "| " + " | ".join(cells) + " |"

        lines.append(row("case wall clock", perf["wall_clock_s"]))
        lines.append(row("search (live, summed per case)", perf["search_live_s"]))
        lines.append(row("LLM (live, summed per case)", perf["llm_live_s"]))
        lines.append(row("RETRIEVE + RANK stages", perf["retrieval_stage_s"]))
        for tool, s in perf["live_latency_ms_by_tool"].items():
            lines.append(row(f"call: {tool}", s, "s", 1000.0))
        for stage, s in perf["stage_s"].items():
            lines.append(row(f"stage: {stage}", s))
        lines.append("")
        per_case = perf.get("per_case") or {}
        if perf.get("cost_usd") is not None:
            cost = f"${perf['cost_usd']:.4f}"
        else:
            models = ", ".join(perf.get("unpriced_models") or []) or "no model calls"
            cost = (
                f"unpriced ({models} — not in llm/pricing.py; a local model runs free "
                "but an unpriced one is reported as unpriced, never as $0)"
            )
        lines.append(
            f"Per case: {per_case.get('calls') or 0:.1f} tool calls, "
            f"{per_case.get('tokens_in') or 0:.0f} input / "
            f"{per_case.get('tokens_out') or 0:.0f} output tokens. Cost: {cost}"
        )
        lines.append("")

    regression = results.get("regression")
    lines.append("## Regression")
    lines.append("")
    if regression and regression.get("rows"):
        lines.append(
            f"Against the baseline saved {regression['baseline_created_at']} "
            f"(git `{regression.get('baseline_git')}`)."
        )
        if regression.get("environment_changed"):
            lines.append("")
            lines.append(
                "**Environment differs from the baseline:** "
                f"{regression['environment_changed']} — deltas reflect that change, "
                "not only code."
            )
        lines.append("")
        lines.append("| metric | baseline | current | delta | |")
        lines.append("|---|---:|---:|---:|---|")
        for row_ in regression["rows"]:
            name = row_["metric"]
            delta = row_["delta"]
            shown = (
                f"{delta:+.1f}s"
                if name.startswith("latency")
                else (f"{delta:+.2f}" if name in _PLAIN else f"{delta * 100:+.1f}%")
            )
            cells = [
                f"`{name}`",
                fmt(name, row_["baseline"]),
                fmt(name, row_["current"]),
                shown,
                "**REGRESSION**" if row_["regression"] else "",
            ]
            lines.append("| " + " | ".join(cells) + " |")
        changes = regression.get("cases") or {}
        lines.append("")
        lines.append(
            f"Newly failing: {', '.join(changes.get('newly_failing') or []) or 'none'}"
        )
        lines.append(
            f"Newly passing: {', '.join(changes.get('newly_passing') or []) or 'none'}"
        )
    else:
        lines.append(regression.get("note") if regression else "No baseline.")
    lines.append("")

    outages = results.get("provider_outages")
    if outages:
        providers = ", ".join(f"{p}: {n}" for p, n in outages["providers"].items())
        lines.append("## Provider outages during the run")
        lines.append("")
        lines.append(
            f"{len(outages['cases'])} case(s) ran while a search provider failed every call "
            f"with no fault injected ({providers}). "
            "The pipeline carries on without it, so these cases describe the system with less "
            "recall than configured. Their metrics beside the rest's — **not a controlled "
            "comparison**: an outage starts partway through a run, so the two groups differ in "
            "case mix (category, difficulty) as well as in providers."
        )
        lines.append("")
        lines.append("| metric | all providers up | n | provider down | n |")
        lines.append("|---|---:|---:|---:|---:|")
        for name, row in outages["comparison"].items():
            lines.append(
                f"| `{name}` | {fmt(name, row['all_providers'])} | {row['all_providers_n']} | "
                f"{fmt(name, row['provider_down'])} | {row['provider_down_n']} |"
            )
        lines.append("")
        lines.append("Affected: " + ", ".join(f"`{c}`" for c in outages["cases"]))
        lines.append("")

    gaps = results.get("capability_gaps") or []
    if gaps:
        lines.append("## Capability gaps")
        lines.append("")
        lines.append(
            "Things a case asked for that this system has no mechanism to do. They are "
            "reported here and kept out of the tool-selection score."
        )
        lines.append("")
        lines.extend(f"- **{gap['gap']}** — {gap['detail']}" for gap in gaps)
        lines.append("")

    lines.append("## Case summary")
    lines.append("")
    lines.append("| case | category | outcome | verdict | checks | failed |")
    lines.append("|---|---|---|---|---:|---|")
    for result in results["cases"]:
        checks = [c for c in result.get("checks") or [] if c["passed"] is not None]
        ok = sum(1 for c in checks if c["passed"])
        verdict = (result.get("answer") or {}).get("verdict") or ""
        state = "skipped" if result["status"] == "skipped" else f"{ok}/{len(checks)}"
        cells = [
            f"`{result['id']}`",
            result["category"],
            result["outcome"],
            verdict,
            state,
            ", ".join(result.get("failed_checks") or []),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    lines.append("")

    failing = [r for r in results["cases"] if r["status"] == "executed" and not r["passed"]]
    lines.append(f"## Failed cases ({len(failing)})")
    lines.append("")
    for result in failing:
        lines.append(case_block(result, {}))
    return "\n".join(lines)
