"""How fast is this substance's literature growing, and does that mean anything.

Pure arithmetic over counts the caller has already loaded. No database, no
network, no settings object — the thresholds arrive as arguments, so the whole
ranking is reproducible from a table of numbers and testable without either.

Every constant in the formula is a guess made before seeing real data. That is
stated rather than hidden because it decides how to read a bad ranking: the
first response to "these candidates are wrong" is to retune here, not to
distrust the counts.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from datetime import date, timedelta

from app.discovery.contracts import ScoredCandidate
from app.discovery.topics import compose_topic, is_usable_angle, rank_outcomes
from app.domain.enums import StudyType

#: Study types that count toward the quality weight. The three the pipeline
#: itself treats as supported-tier evidence — see ``evidence/grading.py``.
_STRONG_TYPES: frozenset[StudyType] = frozenset(
    {StudyType.RCT, StudyType.SYSTEMATIC_REVIEW, StudyType.META_ANALYSIS}
)

#: The same set as stored values, because ``study_mix`` is JSON by the time it
#: reaches the weight — it is written to ``rationale`` and read by the console.
_STRONG_VALUES: frozenset[str] = frozenset(study.value for study in _STRONG_TYPES)

#: Most a full sweep of strong evidence can multiply a score by. Deliberately
#: modest: the quality mix is a tiebreaker between two surges, not a way for six
#: RCTs about a settled substance to outrank a genuine new signal.
_QUALITY_WEIGHT_RANGE = 0.5


def window_starts(window_end: date, window_days: int, count: int) -> list[date]:
    """The starts of the ``count`` complete windows ending before ``window_end``.

    Returned newest-first. These are *historical* buckets used to compute a
    baseline, so the current window is excluded — including it would compare a
    surge against itself and flatten every lift toward 1.
    """
    return [window_end - timedelta(days=window_days * (n + 1)) for n in range(count)]


def baseline_from_buckets(counts: list[int]) -> float:
    """Mean papers per window over the historical buckets.

    A mean, not a median or a max. The median of six mostly-zero buckets is zero
    for anything that was not already established, which makes every newcomer's
    lift identical and destroys the ranking's ability to order them. The max
    would let one busy fortnight two months ago suppress a real surge now.

    An empty list is 0.0 — the caller decides whether that is "no baseline yet"
    (bootstrap, propose nothing) or "genuinely new" (a real, high lift). Those
    are different situations and this function cannot tell them apart, which is
    why ``rank_candidates`` takes ``baseline_windows_available`` separately.
    """
    if not counts:
        return 0.0
    return sum(counts) / len(counts)


def lift(current: int, baseline: float) -> float:
    """Growth against the baseline, Laplace-smoothed.

    ``(current + 1) / (baseline + 1)`` rather than the ratio, for two reasons
    that both bite at exactly the values this scan produces: a zero baseline is
    the *common* case for anything genuinely new, and the raw ratio is undefined
    there; and a baseline of 0.2 papers per fortnight would turn four papers
    into a 20x lift, which is noise wearing a large number.
    """
    return (current + 1) / (baseline + 1)


def quality_weight(study_mix: dict[str, int]) -> float:
    """1.0 to 1.5, by the share of papers that are strong evidence.

    Multiplicative rather than additive so it cannot rescue a descriptor that
    failed the paper floor or has no lift — a single systematic review about a
    substance nobody else is studying is a finding, not a trend, and the same
    quorum reasoning as invariant #3 applies.
    """
    total = sum(study_mix.values())
    if total <= 0:
        return 1.0
    strong = sum(count for name, count in study_mix.items() if name in _STRONG_VALUES)
    return 1.0 + _QUALITY_WEIGHT_RANGE * (strong / total)


def score(current: int, baseline: float, study_mix: dict[str, int]) -> float:
    """Acceleration at scale.

    ``log2(lift) × log2(1 + current) × quality_weight``, and the product is the
    point. The two obvious single-factor rankings both fail, in opposite
    directions and equally badly:

    * **Lift alone** is dominated by tiny denominators. 1 → 4 papers scores
      higher than 30 → 90, so the top of the list is permanently occupied by
      whatever three-paper fluctuation happened this fortnight.
    * **Volume alone** re-proposes vitamin D, creatine and omega-3 forever, in
      that order, because they are always the most-published substances in this
      corpus and always will be.

    Multiplying requires both: something that is growing *and* is already big
    enough for the growth to be more than noise. A negative result is possible
    and meaningful — a substance whose literature is shrinking scores below zero
    and sorts to the bottom, which is correct.
    """
    growth = math.log2(lift(current, baseline))
    scale = math.log2(1 + current)
    return growth * scale * quality_weight(study_mix)


@dataclass(frozen=True, slots=True)
class _Trend:
    """One substance's surge, shared by every angle cut from it.

    Exists so the candidate builder is a module-level function rather than a
    closure over eight loop variables — which works, and is the shape that later
    grows a bug the day someone defers one of these into a comprehension.
    """

    ui: str
    name: str
    papers: int
    baseline: float
    score: float
    lift: float
    study_mix: dict[str, int]
    pmids: list[str]


def _angle_candidate(
    trend: _Trend,
    outcome_ui: str | None,
    outcome_name: str | None,
    angle_papers: int,
) -> ScoredCandidate:
    """One proposal: a substance's trend, narrowed to one question."""
    return ScoredCandidate(
        substance_ui=trend.ui,
        substance_name=trend.name,
        outcome_ui=outcome_ui,
        outcome_name=outcome_name,
        topic=compose_topic(trend.name, outcome_name),
        score=trend.score,
        paper_count=trend.papers,
        # How much of that surge backs *this* question. The reviewer needs both:
        # 8 papers on omega-3 is the reason it is on the desk, 3 of them on skin
        # is what the article would actually rest on.
        angle_paper_count=angle_papers,
        baseline_count=trend.baseline,
        lift=trend.lift,
        study_mix=trend.study_mix,
        top_pmids=trend.pmids,
    )


def rank_candidates(
    *,
    current_counts: dict[str, int],
    baseline_counts: dict[str, float],
    descriptor_names: dict[str, str],
    study_mixes: dict[str, dict[str, int]],
    outcome_cooccurrence: dict[str, dict[str, int]],
    pmids: dict[str, list[str]],
    min_papers: int,
    min_papers_per_angle: int,
    max_angles_per_substance: int,
    baseline_windows_available: int,
    min_baseline_windows: int,
    limit: int,
) -> list[ScoredCandidate]:
    """Rank this window's substances, or refuse to.

    **Returns an empty list when there is not enough history**, whatever the
    counts say. A scan with no baseline can only rank by raw volume, and on day
    one that proposes vitamin D, creatine and omega-3 — the three things any
    reviewer would have thought of unaided, arriving with the authority of a
    ranking. There is deliberately no volume-ranked fallback: the first output
    anyone sees sets whether the tool gets trusted, and being empty is a much
    better first impression than being confidently obvious.

    Args:
        current_counts: Distinct papers per substance UI in this window.
        baseline_counts: Mean papers per historical window, per substance UI.
        descriptor_names: UI → MeSH heading, for substances and outcomes both.
        study_mixes: Substance UI → study-type value → paper count.
        outcome_cooccurrence: Substance UI → outcome UI → co-occurring papers.
        pmids: Substance UI → this window's PMIDs, for the reviewer to check.
        min_papers: Floor below which a substance is not proposed at all.
        min_papers_per_angle: Papers backing one outcome before it is worth a
            separate proposal. Lower than ``min_papers``: that floor asks
            whether the substance is moving, this asks whether there is enough
            to write a specific piece.
        max_angles_per_substance: Ceiling on how many questions one substance
            may occupy the desk with at once.
        baseline_windows_available: Complete historical windows on record.
        min_baseline_windows: How many of those are required to propose anything.
        limit: Maximum candidates returned.
    """
    if baseline_windows_available < min_baseline_windows:
        return []

    scored: list[ScoredCandidate] = []
    for ui, current in current_counts.items():
        if current < min_papers:
            continue
        baseline = baseline_counts.get(ui, 0.0)
        mix = study_mixes.get(ui, {})
        substance_name = descriptor_names.get(ui, ui)
        cooccurrence = outcome_cooccurrence.get(ui, {})

        # One score per *substance*. The trend signal is a fact about the
        # substance — omega-3 is being published on more than usual — and every
        # angle inherits it, because splitting the lift across outcomes would
        # measure something nobody asked about: whether the *pairing* is
        # trending. It would also cost a self-join over the whole history to
        # compute, for a number less useful than the one it replaced.
        substance_score = score(current, baseline, mix)
        substance_lift = lift(current, baseline)

        trend = _Trend(
            ui=ui,
            name=substance_name,
            papers=current,
            baseline=baseline,
            score=substance_score,
            lift=substance_lift,
            study_mix=mix,
            pmids=pmids.get(ui, [])[:5],
        )

        angles = [
            (outcome_ui, outcome_name)
            for outcome_ui, outcome_name in rank_outcomes(cooccurrence, descriptor_names)
            if cooccurrence.get(outcome_ui, 0) >= min_papers_per_angle
            # Depth alone is not enough: a study design or a food can co-occur
            # with a substance on plenty of papers and still name no question.
            # See topics.is_usable_angle.
            and is_usable_angle(substance_name, outcome_name)
        ][:max_angles_per_substance]

        if angles:
            scored.extend(
                _angle_candidate(trend, outcome_ui, name, cooccurrence[outcome_ui])
                for outcome_ui, name in angles
            )
        else:
            # No outcome deep enough to carry a piece of its own. Still worth
            # proposing — the substance is moving — just under a bare topic,
            # which is what every candidate looked like before angles existed.
            scored.append(_angle_candidate(trend, None, None, current))

    # Ties break on angle depth then UI, so a rerun of the same window produces
    # the same list in the same order — a candidate that moved between a dry run
    # and the real one would make the dry run useless as a preview. Angles of one
    # substance share a score, so depth is what orders them against each other.
    scored.sort(
        key=lambda candidate: (
            -candidate.score,
            -candidate.angle_paper_count,
            candidate.substance_ui,
            candidate.outcome_ui or "",
        )
    )
    return scored[:limit]


def study_mix_of(types: list[StudyType]) -> dict[str, int]:
    """Count study types by their stored value.

    Keyed by value rather than by the enum so the result is JSON as it stands —
    it goes straight into ``discovery_candidates.rationale`` and out to the
    console.
    """
    return dict(Counter(study.value for study in types))
