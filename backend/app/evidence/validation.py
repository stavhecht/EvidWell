"""Citation validation — the gate between generation and the review queue.

This module is where invariant #2 stops being a prompt instruction and becomes
a fact. It runs after every synthesis call, before persistence, and it decides
whether a draft is allowed to exist as ``pending_review``.

The design rule it embodies: **the thing that generates must not be the thing
that approves.** Nothing here consults the model, and nothing here trusts the
model's own account of what it cited. Checks run against (a) the exact handle
set that was rendered into the prompt and (b) the database. Neither is
influenceable by generation.

A draft that fails is stored as ``validation_failed`` with a structured report
and never enters the queue. It is not silently dropped — a persistent
validation failure is a prompt bug, and the console surfaces these on their own
tab so the bug is visible rather than absent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import (
    SynthesisInput,
    SynthesisOutput,
    ValidationFailure,
    ValidationReport,
)
from app.domain.enums import StudyType, Verdict
from app.domain.models import Source
from app.evidence.grading import best_grade, max_verdict_for_grade, verdict_exceeds_grade

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResolvedSource:
    """A cited handle successfully mapped to a real, identifiable paper."""

    handle: str
    source_id: str
    pmid: str | None
    doi: str | None
    study_type: StudyType


def check_handles_were_provided(
    output: SynthesisOutput, payload: SynthesisInput
) -> list[ValidationFailure]:
    """Check 1 — every emitted handle was in the prompt.

    The core anti-hallucination check. Compares against ``payload.handle_set``:
    the handles rendered into *this* prompt, not the set of sources in the
    database. A model that invents ``S9`` when only S1–S6 were provided fails
    here, even if some unrelated article once had a valid S9.
    """
    provided = payload.handle_set
    emitted = output.all_cited_handles()
    invented = sorted(emitted - provided, key=_handle_sort_key)

    return [
        ValidationFailure(
            code="hallucinated_handle",
            message=(
                f"cited {handle}, which was not among the sources provided "
                f"({_format_handles(provided)})"
            ),
            detail={"handle": handle, "provided": sorted(provided, key=_handle_sort_key)},
        )
        for handle in invented
    ]


async def check_sources_resolve(
    session: AsyncSession, payload: SynthesisInput, cited: set[str]
) -> tuple[list[ResolvedSource], list[ValidationFailure]]:
    """Check 2 — every cited source maps to a row with a real PMID or DOI.

    Handle → source_id comes from the prompt payload; the row is then loaded
    and its identifiers confirmed. A source row cannot exist without at least
    one identifier (there is a CHECK constraint), so this is belt-and-braces
    against a row deleted between retrieval and validation — but it is also
    what makes "4/4 citations resolve" a statement about reality rather than
    about the prompt.

    Handles not present in the payload are skipped here; check 1 already
    reported them, and reporting the same defect twice makes the report harder
    to read, not more convincing.
    """
    by_handle = {source.handle: source for source in payload.sources}
    wanted = {handle: by_handle[handle] for handle in cited if handle in by_handle}
    if not wanted:
        return [], []

    result = await session.execute(
        select(Source).where(Source.id.in_([s.source_id for s in wanted.values()]))
    )
    rows = {row.id: row for row in result.scalars()}

    resolved: list[ResolvedSource] = []
    failures: list[ValidationFailure] = []

    for handle, prompt_source in sorted(wanted.items(), key=lambda kv: _handle_sort_key(kv[0])):
        row = rows.get(prompt_source.source_id)
        if row is None:
            failures.append(
                ValidationFailure(
                    code="unresolvable_source",
                    message=f"{handle} refers to a source that no longer exists",
                    detail={"handle": handle, "source_id": prompt_source.source_id},
                )
            )
            continue
        if not row.pmid and not row.doi:
            failures.append(
                ValidationFailure(
                    code="unresolvable_source",
                    message=f"{handle} has neither a PMID nor a DOI",
                    detail={"handle": handle, "source_id": prompt_source.source_id},
                )
            )
            continue
        resolved.append(
            ResolvedSource(
                handle=handle,
                source_id=row.id,
                pmid=row.pmid,
                doi=row.doi,
                study_type=StudyType(row.study_type),
            )
        )

    return resolved, failures


def check_beats_are_cited(output: SynthesisOutput) -> list[ValidationFailure]:
    """Check 3 — the evidence beat carries at least one citation.

    An uncited factual sentence is exactly the failure this whole system exists
    to prevent, so beat 2 (what the research shows) must cite something.

    Two deliberate exemptions:

    * verdict ``no_evidence`` — an article correctly reporting that nothing was
      found has nothing to cite, and demanding a citation would push the model
      toward citing something irrelevant to satisfy the check.
    * beats 1 and 3 — beat 1 restates the product's own marketing claim and
      beat 3 is an interpretive bottom line; requiring citations there
      encourages decorative citation, which is worse than none.
    """
    if output.verdict is Verdict.NO_EVIDENCE:
        return []

    from app.domain.contracts import extract_handles

    if not extract_handles(output.body.beat_2_evidence):
        return [
            ValidationFailure(
                code="uncited_beat",
                message="the evidence beat states findings without citing any source",
                detail={"beat": "beat_2_evidence"},
            )
        ]
    return []


def check_verdict_within_grade(
    output: SynthesisOutput, resolved: list[ResolvedSource]
) -> tuple[StudyType, list[ValidationFailure]]:
    """Check 4 — invariant #3: the verdict does not exceed its evidence.

    ``best_grade`` is computed over the **cited** sources only. Sources the
    model retrieved but ignored cannot raise its ceiling — otherwise a strong
    review sitting unused in the prompt would license a confident verdict the
    article never actually supported.

    This is what makes "honest about evidence strength" structural. A
    ``supported`` verdict resting on two cell-culture studies fails here and the
    draft never reaches a human. It is not a style note and it is not
    overridable in the editor: a reviewer can rewrite the article, but cannot
    approve a draft that was never allowed into the queue.
    """
    grade = best_grade([source.study_type for source in resolved])

    if verdict_exceeds_grade(output.verdict, grade):
        return grade, [
            ValidationFailure(
                code="verdict_exceeds_grade",
                message=(
                    f"verdict '{output.verdict}' exceeds what the cited evidence "
                    f"supports (best study type: {grade}, ceiling: "
                    f"{max_verdict_for_grade(grade)})"
                ),
                detail={
                    "verdict": str(output.verdict),
                    "best_grade": str(grade),
                    "ceiling": str(max_verdict_for_grade(grade)),
                },
            )
        ]
    return grade, []


async def validate_draft(
    session: AsyncSession,
    output: SynthesisOutput,
    payload: SynthesisInput,
) -> ValidationReport:
    """Run every check and produce the report stored on the article.

    Runs all checks rather than short-circuiting on the first failure: when a
    draft is bad you want the whole picture in one report, not one symptom per
    re-run.

    Returns:
        A report whose ``passed`` decides the article's status:
        True → ``pending_review``, False → ``validation_failed``.
    """
    failures: list[ValidationFailure] = []

    failures.extend(check_handles_were_provided(output, payload))

    cited = output.all_cited_handles()
    resolved, resolve_failures = await check_sources_resolve(session, payload, cited)
    failures.extend(resolve_failures)

    failures.extend(check_beats_are_cited(output))

    grade, verdict_failures = check_verdict_within_grade(output, resolved)
    failures.extend(verdict_failures)

    report = ValidationReport(
        passed=not failures,
        citations_total=len(cited),
        citations_resolved=len(resolved),
        best_evidence_grade=grade,
        failures=failures,
    )

    if report.passed:
        logger.info("draft validated: %s, grade=%s", report.badge, grade)
    else:
        logger.warning(
            "draft REJECTED (%d failures): %s", len(failures), summarise_failures(report)
        )
    return report


def summarise_failures(report: ValidationReport) -> str:
    """One-line human summary for the console list view."""
    if report.passed:
        return "passed"

    counts: dict[str, int] = {}
    for failure in report.failures:
        counts[failure.code] = counts.get(failure.code, 0) + 1

    labels = {
        "hallucinated_handle": "hallucinated citation",
        "unresolvable_source": "unresolvable source",
        "uncited_beat": "uncited evidence beat",
        "verdict_exceeds_grade": "verdict exceeds evidence grade",
        "malformed_body": "unparseable body",
    }
    parts = [
        f"{count} {labels.get(code, code)}{'s' if count > 1 else ''}"
        for code, count in sorted(counts.items())
    ]
    return ", ".join(parts)


def _handle_sort_key(handle: str) -> int:
    """Sort S2 before S10. Lexicographic order would not."""
    digits = handle[1:]
    return int(digits) if digits.isdigit() else 0


def _format_handles(handles: set[str]) -> str:
    return ", ".join(sorted(handles, key=_handle_sort_key)) or "none"
