"""Scoring and selection. Pure functions: no I/O, no model, no clock.

Four separate questions, kept separate:

* **trend signal** — are people becoming interested? (``trend_growth``,
  ``news_momentum``, ``reader_interest``)
* **content availability** — is there material to research? (a gate, not a
  weight: ``content_sources``)
* **scientific evidence** — can we responsibly say anything? (``evidence_score``
  and ``evidence_status``, from PubMed/Europe PMC counts only)
* **article potential** — is it worth writing now? (``novelty``, plus
  ``source_quality``)

``overall`` is a weighted mean of the components that were **measured**. A
component with no data is left out and the remaining weights renormalised —
never filled with a guess — and ``Scores.unavailable`` lists what was left out.
The evidence component is the exception: without it there is no score at all,
because a health topic nobody checked the literature for cannot be ranked
against ones that were checked.

All component scores are 0-100. The constants below are starting values chosen
before any run existed, like ``discovery/scoring.py``'s; the desk shows the
components rather than only the total so a reviewer can see what they rest on.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from urllib.parse import urlparse

from app.research.contracts import (
    EVIDENCE_RANK,
    CandidateTopic,
    EvidenceCounts,
    EvidenceStatus,
    NewsSignal,
    Scores,
    TrendSignal,
    WebResult,
    WebSignal,
)
from app.research.normalize import query_key

TREND_GROWTH = "trend_growth"
NEWS_MOMENTUM = "news_momentum"
SCIENTIFIC_EVIDENCE = "scientific_evidence"
SOURCE_QUALITY = "source_quality"
READER_INTEREST = "reader_interest"
NOVELTY = "novelty"
COMPONENTS = (
    TREND_GROWTH,
    NEWS_MOMENTUM,
    SCIENTIFIC_EVIDENCE,
    SOURCE_QUALITY,
    READER_INTEREST,
    NOVELTY,
)

#: Below this similarity to anything recent a topic counts as wholly new.
NOVELTY_FLOOR = 0.5


def clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def log_ratio_score(current: float, previous: float) -> float:
    """50 when flat, +25 per doubling, -25 per halving, clamped to 0-100.

    Laplace-smoothed so 0 -> 3 reads as a rise rather than a division by zero,
    and so 1 -> 4 does not outrank 30 -> 90 by as much as a raw ratio would.
    """
    return clamp(50.0 + 25.0 * math.log2((current + 1.0) / (previous + 1.0)))


def growth_percent(current: float, previous: float) -> float | None:
    if previous <= 0:
        return None
    return round((current - previous) / previous * 100.0, 1)


# --- components ------------------------------------------------------------


def trend_growth_score(trend: TrendSignal | None) -> float | None:
    """Momentum in search interest: change, not level.

    Topic A going 40 -> 80 scores 75; topic B going 90 -> 93 scores ~51. The
    interest series is preferred; a query too small for Trends to chart (all
    zeros) falls back to the "rising" percent Trends reported for it.
    """
    if trend is None:
        return None
    if trend.is_breakout:
        return 100.0
    if (
        trend.current_interest is not None
        and trend.previous_interest is not None
        and trend.current_interest + trend.previous_interest > 0
    ):
        return round(log_ratio_score(trend.current_interest, trend.previous_interest), 1)
    if trend.rising_percent is not None:
        return round(clamp(50.0 + 25.0 * math.log2(1.0 + trend.rising_percent / 100.0)), 1)
    return None


def reader_interest_score(trend: TrendSignal | None) -> float | None:
    """Search-interest *level*, against the anchor term in the same request.

    Equal to the anchor scores 50, four times it 100, a quarter of it 0. This
    is search interest standing in for reader interest; community signals
    (Reddit and the like) are a future provider.
    """
    if trend is None or trend.current_interest is None or trend.anchor_interest is None:
        return None
    ratio = (trend.current_interest + 0.5) / (trend.anchor_interest + 0.5)
    return round(clamp(50.0 + 25.0 * math.log2(ratio)), 1)


def news_momentum_score(news: NewsSignal | None) -> float | None:
    """Half coverage growth, half coverage volume.

    Volume alone would re-propose whatever is always in the news; growth alone
    would let one article after a quiet week look like a wave. When the earlier
    window's count is unknown (the provider's page filled up first), volume is
    all there is and all that is used.
    """
    if news is None:
        return None
    volume = clamp(25.0 * math.log2(1.0 + news.recent))
    if news.previous is None:
        return round(volume, 1)
    return round(0.5 * log_ratio_score(news.recent, news.previous) + 0.5 * volume, 1)


def evidence_score(counts: EvidenceCounts | None) -> float | None:
    """40 for reviews/meta-analyses (full at 3), 35 for RCTs (full at 5), 25 for
    the size of the literature (full at 30 papers)."""
    if counts is None:
        return None
    return round(
        40.0 * min(1.0, counts.reviews_or_meta / 3.0)
        + 35.0 * min(1.0, counts.rcts / 5.0)
        + 25.0 * min(1.0, counts.total / 30.0),
        1,
    )


def evidence_status(counts: EvidenceCounts | None) -> EvidenceStatus | None:
    """What can responsibly be said, from counts alone.

    * strong: at least 2 reviews/meta-analyses and 3 randomised trials
    * moderate: at least 1 review/meta-analysis, or 3 trials
    * emerging: at least 1 trial, or 10+ papers with 3+ from the last year
    * limited: some literature, none of the above
    * none: nothing at all
    """
    if counts is None:
        return None
    if counts.reviews_or_meta >= 2 and counts.rcts >= 3:
        return EvidenceStatus.STRONG
    if counts.reviews_or_meta >= 1 or counts.rcts >= 3:
        return EvidenceStatus.MODERATE
    if counts.rcts >= 1 or (counts.total >= 10 and counts.recent >= 3):
        return EvidenceStatus.EMERGING
    if counts.total >= 1:
        return EvidenceStatus.LIMITED
    return EvidenceStatus.NONE


def meets(status: EvidenceStatus | None, minimum: EvidenceStatus) -> bool:
    return status is not None and EVIDENCE_RANK[status] >= EVIDENCE_RANK[minimum]


def source_quality_score(web: WebSignal | None) -> float | None:
    """Share of relevant web results from authoritative domains."""
    if web is None:
        return None
    if web.relevant_results == 0:
        return 0.0
    return round(100.0 * web.authoritative_results / web.relevant_results, 1)


def novelty_score(max_similarity: float | None, duplicate_threshold: float) -> float | None:
    """100 for nothing similar in recent output, 0 at the duplicate threshold.

    Mapped from similarity rather than ``100 * (1 - similarity)``, because two
    unrelated wellness topics still embed around 0.4-0.6 apart and the raw
    form would score every topic "half novel".
    """
    if max_similarity is None:
        return None
    span = duplicate_threshold - NOVELTY_FLOOR
    return round(clamp(100.0 * (duplicate_threshold - max_similarity) / span), 1)


def web_signal(
    results: list[WebResult],
    *,
    source: str,
    subject: str,
    authoritative: Iterable[str],
) -> WebSignal:
    """Summarise one search: how much, how relevant, from how many places.

    A result is relevant when its title or snippet names the subject — every
    word of it — which is crude and deliberately so: it only has to separate
    "a page about creatine" from a page the search engine padded the list with.
    """
    words = [word for word in subject.lower().split() if word]
    domains = [d.lower() for d in authoritative]
    relevant = [
        result
        for result in results
        if all(word in f"{result.title} {result.snippet}".lower() for word in words)
    ]
    hosts = {_host(result.url) for result in relevant}
    trusted = [result for result in relevant if _is_authoritative(_host(result.url), domains)]
    return WebSignal(
        source=source,
        results_found=len(results),
        relevant_results=len(relevant),
        distinct_domains=len(hosts - {""}),
        authoritative_results=len(trusted),
        top_results=(trusted + [r for r in relevant if r not in trusted])[:5],
    )


def _host(url: str) -> str:
    host = urlparse(url).hostname or ""
    return host.lower().removeprefix("www.")


def _is_authoritative(host: str, domains: list[str]) -> bool:
    for domain in domains:
        if domain.startswith("."):
            if host.endswith(domain):
                return True
        elif host == domain or host.endswith("." + domain):
            return True
    return False


def content_sources(candidate: CandidateTopic) -> int | None:
    """Relevant web results plus news articles; ``None`` when neither was measured."""
    if candidate.web is None and candidate.news is None:
        return None
    web = candidate.web.relevant_results if candidate.web else 0
    news = candidate.news.recent + (candidate.news.previous or 0) if candidate.news else 0
    return web + news


# --- overall ---------------------------------------------------------------


def score(
    candidate: CandidateTopic,
    weights: Mapping[str, float],
    *,
    duplicate_threshold: float,
) -> Scores:
    """Every component that could be measured, and their renormalised mean."""
    measured: dict[str, float | None] = {
        TREND_GROWTH: trend_growth_score(candidate.trend),
        NEWS_MOMENTUM: news_momentum_score(candidate.news),
        SCIENTIFIC_EVIDENCE: evidence_score(candidate.science.best),
        SOURCE_QUALITY: source_quality_score(candidate.web),
        READER_INTEREST: reader_interest_score(candidate.trend),
        NOVELTY: novelty_score(
            candidate.novelty.max_similarity if candidate.novelty else None,
            duplicate_threshold,
        ),
    }
    return combine(measured, weights)


def combine(measured: Mapping[str, float | None], weights: Mapping[str, float]) -> Scores:
    components = {name: value for name, value in measured.items() if value is not None}
    unavailable = [name for name in COMPONENTS if measured.get(name) is None]
    if SCIENTIFIC_EVIDENCE not in components:
        # Unchecked is not "weak evidence" — it is no ranking at all.
        return Scores(components=components, unavailable=unavailable, overall=None)
    total_weight = sum(weights[name] for name in components)
    weights_used = {name: round(weights[name] / total_weight, 4) for name in components}
    overall = sum(components[name] * weights_used[name] for name in components)
    return Scores(
        components=components,
        unavailable=unavailable,
        weights_used=weights_used,
        overall=round(overall, 2),
    )


def rank(candidates: list[CandidateTopic]) -> list[CandidateTopic]:
    """Live, scored candidates, best first. Ties break on evidence, then name."""
    scored = [c for c in candidates if c.live and c.scores.overall is not None]
    return sorted(
        scored,
        key=lambda c: (
            -(c.scores.overall or 0.0),
            -c.scores.components.get(SCIENTIFIC_EVIDENCE, 0.0),
            c.canonical_topic.lower(),
        ),
    )


def gate_reason(
    candidate: CandidateTopic, *, min_status: EvidenceStatus, min_sources: int
) -> str | None:
    """Why a candidate may not be selected, or None.

    The evidence gate is what keeps a viral topic with no literature off the
    desk however high its trend score: popularity is not evidence.
    """
    if candidate.science.best is None:
        return "scientific evidence could not be checked (PubMed and Europe PMC failed)"
    status = candidate.evidence_status
    if not meets(status, min_status):
        counts = candidate.science.best
        return (
            f"evidence {status.value if status else 'unknown'} "
            f"({counts.total} papers, {counts.reviews_or_meta} reviews/meta-analyses, "
            f"{counts.rcts} RCTs); needs {min_status.value}"
        )
    sources = content_sources(candidate)
    if sources is not None and sources < min_sources:
        return (
            f"too little material to research ({sources} web/news sources; needs {min_sources})"
        )
    return None


def select(
    ranked: list[CandidateTopic], *, target: int, max_per_subject: int = 1
) -> list[CandidateTopic]:
    """The top ``target`` candidates, at most ``max_per_subject`` per subject.

    One per subject by default: two creatine angles in one week's proposals is
    one topic proposed twice, and it crowds out a different subject.
    """
    chosen: list[CandidateTopic] = []
    for candidate in ranked:
        words = _subject_words(candidate.subject)
        # One subject containing the other's words is the same subject:
        # measured, "blood pressure" and "blood pressure medication" were both
        # selected under an exact-match rule.
        same = sum(
            1
            for other in chosen
            if words <= _subject_words(other.subject) or _subject_words(other.subject) <= words
        )
        if same >= max_per_subject:
            continue
        chosen.append(candidate)
        if len(chosen) == target:
            break
    return chosen


def _subject_words(subject: str) -> frozenset[str]:
    return frozenset(query_key(subject).split()) or frozenset({subject.strip().lower()})
