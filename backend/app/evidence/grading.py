"""Evidence-quality grading, and the verdict ceiling it implies.

Hierarchy (weakest → strongest):
    in-vitro / animal < observational < RCT < systematic review / meta-analysis

Two jobs:

1. **Classify** a paper into a ``StudyType`` from whatever metadata the
   provider gave us. Best-effort, and biased toward under-claiming.
2. **Cap** verdict confidence by the strongest evidence actually cited. This is
   invariant #3 and it is not advisory — a verdict above its cap is a
   validation failure that keeps the draft out of the review queue.

The small pure functions here are implemented rather than stubbed: they *are*
the specification of the invariant, and expressing them as prose in a docstring
would leave the most important rule in the system as the least precise thing in
it.
"""

from __future__ import annotations

import re

from app.domain.enums import EVIDENCE_RANK, StudyType, Verdict

#: Verdict ceiling by the strongest study type among cited sources.
#:
#: Reading: an article whose best source is an observational study may be
#: 'mixed' at most — never 'supported' — because observational data cannot
#: establish cause and effect. An article resting on cell-culture or animal
#: work may be 'weak' at most: the research has not been done in people.
#:
#: UNKNOWN maps to WEAK for the same reason UNKNOWN sorts lowest in the
#: hierarchy — uncertainty about study quality must reduce confidence, never
#: license it.
VERDICT_CEILING: dict[StudyType, Verdict] = {
    StudyType.META_ANALYSIS: Verdict.SUPPORTED,
    StudyType.SYSTEMATIC_REVIEW: Verdict.SUPPORTED,
    StudyType.RCT: Verdict.SUPPORTED,
    StudyType.OBSERVATIONAL: Verdict.MIXED,
    StudyType.CASE_REPORT: Verdict.WEAK,
    StudyType.ANIMAL: Verdict.WEAK,
    StudyType.IN_VITRO: Verdict.WEAK,
    StudyType.UNKNOWN: Verdict.WEAK,
}

#: Confidence ordering of verdicts, weakest to strongest claim about evidence.
#: NO_EVIDENCE is the *least* confident assertion of support and is therefore
#: always permissible — an article may always report finding nothing.
VERDICT_STRENGTH: dict[Verdict, int] = {
    Verdict.NO_EVIDENCE: 0,
    Verdict.WEAK: 1,
    Verdict.MIXED: 2,
    Verdict.SUPPORTED: 3,
}

#: PubMed publication types → our hierarchy. Matched case-insensitively.
PUBLICATION_TYPE_MAP: dict[str, StudyType] = {
    "meta-analysis": StudyType.META_ANALYSIS,
    "systematic review": StudyType.SYSTEMATIC_REVIEW,
    "randomized controlled trial": StudyType.RCT,
    "controlled clinical trial": StudyType.RCT,
    "clinical trial, phase iii": StudyType.RCT,
    "clinical trial, phase ii": StudyType.RCT,
    "observational study": StudyType.OBSERVATIONAL,
    "cohort studies": StudyType.OBSERVATIONAL,
    "case-control studies": StudyType.OBSERVATIONAL,
    "cross-sectional studies": StudyType.OBSERVATIONAL,
    "case reports": StudyType.CASE_REPORT,
}

#: Title/abstract fallbacks, checked only when publication type is unusable.
#: Ordered strongest-first; first match wins.
TEXT_PATTERNS: list[tuple[re.Pattern[str], StudyType]] = [
    (re.compile(r"\bmeta-?analys(is|es)\b", re.I), StudyType.META_ANALYSIS),
    (re.compile(r"\bsystematic review\b", re.I), StudyType.SYSTEMATIC_REVIEW),
    (
        re.compile(r"\b(randomi[sz]ed|double-?blind|placebo-?controlled)\b", re.I),
        StudyType.RCT,
    ),
    (re.compile(r"\b(cohort|case-?control|cross-?sectional)\b", re.I), StudyType.OBSERVATIONAL),
    (re.compile(r"\b(in vitro|cell culture|cell line)\b", re.I), StudyType.IN_VITRO),
    (re.compile(r"\b(mice|mouse|rats?|murine|rodent|zebrafish)\b", re.I), StudyType.ANIMAL),
]


def max_verdict_for_grade(best_grade: StudyType) -> Verdict:
    """The strongest verdict this evidence permits."""
    return VERDICT_CEILING[best_grade]


def verdict_exceeds_grade(verdict: Verdict, best_grade: StudyType) -> bool:
    """True when a verdict claims more confidence than the evidence supports.

    This is the check behind invariant #3. A True here fails the draft.
    """
    return VERDICT_STRENGTH[verdict] > VERDICT_STRENGTH[max_verdict_for_grade(best_grade)]


def best_grade(study_types: list[StudyType]) -> StudyType:
    """The strongest study type in a set of cited sources.

    Empty input yields UNKNOWN, which caps the verdict at 'weak' — the correct
    posture for an article that cited nothing.
    """
    if not study_types:
        return StudyType.UNKNOWN
    return max(study_types, key=lambda s: EVIDENCE_RANK[s])


def classify_study_type(
    publication_types: list[str],
    title: str = "",
    abstract: str = "",
) -> StudyType:
    """Best-effort classification from provider metadata.

    Publication types are authoritative when present and recognised; title and
    abstract text are a fallback for providers that don't supply them (OpenAlex
    and Semantic Scholar, mostly).

    Deliberately conservative. Returning UNKNOWN caps the verdict at 'weak',
    which is a safe failure; guessing RCT from an ambiguous abstract unlocks
    'supported' on evidence that may not warrant it. When torn, prefer the
    weaker classification.

    One asymmetry worth noting: within *publication types* the strongest match
    wins (PubMed tags a paper "Meta-Analysis" and "Review" both, and the former
    is the real signal), but the *text* fallback takes the first match in a
    strongest-first list and stops. Abstract prose is far less reliable than a
    curated tag, so it gets one shot rather than a survey.
    """
    matches = [
        PUBLICATION_TYPE_MAP[normalised]
        for publication_type in publication_types
        if (normalised := publication_type.strip().lower()) in PUBLICATION_TYPE_MAP
    ]
    if matches:
        return max(matches, key=lambda study: EVIDENCE_RANK[study])

    haystack = f"{title} {abstract}"
    for pattern, study_type in TEXT_PATTERNS:
        if pattern.search(haystack):
            return study_type

    return StudyType.UNKNOWN


def is_weak_evidence(study_type: StudyType) -> bool:
    """Whether the review UI should flag this source inline.

    Drives the 'this verdict rests on weak study types' warning in the sources
    panel — the reviewer's cue to look harder.
    """
    return EVIDENCE_RANK[study_type] < EVIDENCE_RANK[StudyType.OBSERVATIONAL]
