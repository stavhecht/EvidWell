"""APPRAISE: how much it labelled, and how often a label matches a hand label.

The labels say which way each ranked source points for a claim (supports, no
effect, contradicts, unclear, off topic). They are recorded and shown to
reviewers, and gate nothing yet. These numbers are what decide whether they
ever should (CLAUDE.md, APPRAISE):

* **coverage** — labelled pairs over offered pairs. A pair the model skipped,
  or whose call failed, has no label; it must not be scored as wrong or right.
* **accuracy** — exact match against ``expected_stances`` on the case.
* **direction accuracy** — the same, after folding the labels into the three
  directions a verdict could act on (for / against / neither). ``no_effect``
  and ``contradicts`` both count against a claim, so confusing them is a much
  smaller error than calling a null trial supportive.
* **disagreement** — the final draft's verdict pointed the other way from its
  appraised sources (a ``check_verdict_against_stance`` warning).
"""

from __future__ import annotations

from typing import Any

from app.evidence.validation import STANCE_WARNING_CODES
from evaluation.metrics.retrieval import identity_keys

_DIRECTION = {
    "supports": "for",
    "no_effect": "against",
    "contradicts": "against",
    "unclear": "neither",
    "off_topic": "neither",
}


def evaluate(trace: dict[str, Any], expected: dict[str, dict[str, str]]) -> dict[str, Any]:
    """Per-case appraisal metrics over the final retrieval round.

    ``expected`` is claim -> paper id -> stance (``EvalCase.expected_stances``).
    """
    span = next(
        (s for s in reversed(trace.get("stages") or []) if s.get("stage") == "appraise"),
        None,
    )
    rounds = trace.get("rounds") or []
    if span is None or not rounds:
        return {"applicable": False}

    stances: dict[str, dict[str, str]] = trace.get("stances") or {}
    papers: dict[str, dict[str, Any]] = trace.get("papers") or {}
    final = rounds[-1]

    offered = labelled = scored = correct = direction_correct = unlabelled_gold = 0
    mismatches: list[dict[str, Any]] = []
    for claim, entries in final["ranked"].items():
        labels = stances.get(claim, {})
        claim_gold = expected.get(claim, {})
        for entry in entries:
            offered += 1
            predicted = labels.get(entry["source_id"])
            if predicted is not None:
                labelled += 1
            paper = papers.get(entry["source_id"])
            if paper is None:
                continue
            gold = next(
                (claim_gold[key] for key in sorted(identity_keys(paper)) if key in claim_gold),
                None,
            )
            if gold is None:
                continue
            if predicted is None:
                unlabelled_gold += 1
                continue
            scored += 1
            correct += predicted == gold
            direction_correct += _DIRECTION[predicted] == _DIRECTION[gold]
            if predicted != gold:
                mismatches.append(
                    {
                        "claim": claim,
                        "handle": entry.get("handle"),
                        "pmid": paper.get("pmid"),
                        "doi": paper.get("doi"),
                        "title": paper.get("title"),
                        "expected": gold,
                        "got": predicted,
                    }
                )

    validation = trace.get("final_validation") or {}
    tally = validation.get("stance_tally")
    warnings = {w.get("code") for w in validation.get("warnings") or []}
    return {
        "applicable": True,
        "cause": (span.get("metrics") or {}).get("cause"),
        "offered": offered,
        "labelled": labelled,
        "coverage": labelled / offered if offered else None,
        "scored": scored,
        "correct": correct,
        "accuracy": correct / scored if scored else None,
        "direction_correct": direction_correct,
        "direction_accuracy": direction_correct / scored if scored else None,
        # Gold papers that were ranked but never labelled: neither right nor
        # wrong, and a sign coverage is costing the measurement its data.
        "unlabelled_gold": unlabelled_gold,
        # Expected claims extraction did not produce, worded exactly: not scored.
        "unmatched_claims": sorted(set(expected) - set(final["ranked"])),
        "mismatches": mismatches,
        # None when the final draft carried no appraisal to disagree with.
        "disagrees": None if tally is None else bool(warnings & STANCE_WARNING_CODES),
    }
