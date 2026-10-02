"""Shapes the research agent passes around, and the state its graph threads.

Every signal is a record of what a provider actually returned. A field that is
``None`` means "not measured" and is never a stand-in for zero: the scorer
drops unmeasured components and renormalises, and says so in
``Scores.unavailable``.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings
from app.domain.enums import ResearchCandidateStatus, ResearchRunMode, Subject


# A research topic's category is the article category it would be filed under,
# so the members are exactly ``Subject``'s — checked below at import, so the
# desk's trending-topics filter, the feed drawer and the reviewer's picker
# cannot drift apart.
#
# A separate class rather than ``Category = Subject`` because a Pydantic enum's
# docstring becomes its JSON-schema ``description``, and ``TriageOutput``'s
# schema is the triage model's generation grammar: ``Subject``'s docstring
# (colours, pictures, reviewers) is maintainer rationale the model must not
# read. So this class deliberately has no docstring; comments only.
class Category(StrEnum):
    FITNESS = "fitness"
    NUTRITION = "nutrition"
    SUPPLEMENTS = "supplements"
    SLEEP_RECOVERY = "sleep_recovery"
    LIFESTYLE = "lifestyle"
    PREVENTIVE_HEALTH = "preventive_health"
    GENERAL_HEALTH = "general_health"
    WELLNESS = "wellness"
    OTHER = "other"


if [c.value for c in Category] != [s.value for s in Subject]:
    raise RuntimeError(
        "research Category must list exactly the article Subject values, in "
        "order; update app/research/contracts.py with domain/enums.py"
    )


class EvidenceStatus(StrEnum):
    """How much credible literature a topic has, weakest first.

    Declaration order is the ranking (``EVIDENCE_RANK``). Derived from counts
    by ``scoring.evidence_status`` — never from a model's opinion.
    """

    NONE = "none"
    LIMITED = "limited"
    EMERGING = "emerging"
    MODERATE = "moderate"
    STRONG = "strong"


EVIDENCE_RANK: dict[EvidenceStatus, int] = {
    status: rank for rank, status in enumerate(EvidenceStatus)
}


class ResearchStage(StrEnum):
    """Where a run is. Written to ``research_runs.stage`` as it moves."""

    QUEUED = "queued"
    LOADING_CONFIG = "loading_config"
    DISCOVERING_TRENDS = "discovering_trends"
    EXPANDING_TOPICS = "expanding_topics"
    BUILDING_CANDIDATES = "building_candidates"
    CHECKING_NOVELTY = "checking_novelty"
    RESEARCHING_NEWS = "researching_news"
    RESEARCHING_SCIENCE = "researching_science"
    SCORING_TOPICS = "scoring_topics"
    SEARCHING_WEB = "searching_web"
    SHORTLISTING = "shortlisting"
    DEEP_RESEARCH = "deep_research"
    SELECTING_TOPICS = "selecting_topics"
    BACKFILLING = "backfilling"
    COMPLETED = "completed"
    FAILED = "failed"


# --- request ---------------------------------------------------------------


class ResearchParams(BaseModel):
    """What a trigger may ask for. Anything omitted falls back to settings.

    The same model for the desk button and for n8n's weekly call, so the two
    cannot drift into asking different things.
    """

    target_article_count: int | None = Field(default=None, ge=1, le=10)
    categories: list[Category] | None = None
    trend_window_days: int | None = Field(default=None, ge=1, le=30)
    geo: str | None = Field(default=None, min_length=2, max_length=2)
    language: str | None = Field(default=None, min_length=2, max_length=2)


class ResearchConfig(BaseModel):
    """Everything a run scores and selects with, frozen when it is created.

    Stored on ``research_runs.config`` so a run can be explained later even
    after the settings changed.
    """

    target_article_count: int
    min_article_count: int
    max_article_count: int
    max_candidates: int
    web_pool_size: int
    shortlist_size: int
    trend_window_days: int
    news_window_days: int
    scientific_window_days: int
    weights: dict[str, float]
    min_evidence_status: EvidenceStatus
    min_sources: int
    cluster_threshold: float
    duplicate_threshold: float
    novelty_lookback_days: int
    trends_anchor: str
    expand_top: int
    geo: str
    language: str
    categories: list[Category]
    authoritative_domains: list[str]

    @classmethod
    def resolve(cls, settings: Settings, params: ResearchParams) -> ResearchConfig:
        low, high = settings.research_min_article_count, settings.research_max_article_count
        target = params.target_article_count or settings.research_target_article_count
        target = max(low, min(high, target))
        return cls(
            target_article_count=target,
            # A request for fewer than the configured minimum lowers the
            # minimum with it; asking for 2 and being told "under target" at 3
            # would be nonsense.
            min_article_count=min(low, target),
            max_article_count=high,
            max_candidates=settings.research_max_candidates,
            web_pool_size=settings.research_web_pool_size,
            shortlist_size=settings.research_shortlist_size,
            trend_window_days=params.trend_window_days or settings.research_trend_window_days,
            news_window_days=settings.research_news_window_days,
            scientific_window_days=settings.research_scientific_window_days,
            weights={
                "trend_growth": settings.research_weight_trend,
                "news_momentum": settings.research_weight_news,
                "scientific_evidence": settings.research_weight_evidence,
                "source_quality": settings.research_weight_source_quality,
                "reader_interest": settings.research_weight_reader_interest,
                "novelty": settings.research_weight_novelty,
            },
            min_evidence_status=EvidenceStatus(settings.research_min_evidence_status),
            min_sources=settings.research_min_sources,
            cluster_threshold=settings.research_cluster_threshold,
            duplicate_threshold=settings.research_duplicate_threshold,
            novelty_lookback_days=settings.research_novelty_lookback_days,
            trends_anchor=settings.research_trends_anchor,
            expand_top=settings.research_expand_top,
            geo=(params.geo or settings.research_geo).upper(),
            language=(params.language or settings.research_language).lower(),
            categories=params.categories or list(Category),
            authoritative_domains=[d.lower() for d in settings.research_authoritative_domains],
        )


# --- provider results ------------------------------------------------------


class RawTrend(BaseModel):
    """One query Google Trends (or another trend provider) reported rising."""

    query: str
    seed: str
    category: Category
    source: str
    #: Trends' "rising" value: percent growth against the preceding period.
    rising_percent: int | None = None
    #: Trends labels growth above 5000% "Breakout".
    is_breakout: bool = False
    #: 0 for a seed's own related queries, 1 for an expansion of one of them.
    depth: int = 0


class TrendSignal(BaseModel):
    source: str
    #: Mean interest over the trend window, on the anchored request's scale.
    current_interest: float | None = None
    previous_interest: float | None = None
    #: The anchor term's current mean in the same request, for the level score.
    anchor_interest: float | None = None
    growth_percent: float | None = None
    rising_percent: int | None = None
    is_breakout: bool = False
    #: Which measurement ``growth_percent`` came from.
    growth_source: str | None = None
    related_queries: list[str] = Field(default_factory=list)


class WebResult(BaseModel):
    title: str
    url: str
    snippet: str = ""


class WebSignal(BaseModel):
    source: str
    results_found: int
    relevant_results: int
    distinct_domains: int
    authoritative_results: int
    top_results: list[WebResult] = Field(default_factory=list)


class NewsArticle(BaseModel):
    title: str
    url: str
    source: str | None = None
    published_at: datetime | None = None


class NewsSignal(BaseModel):
    source: str
    #: Everything the provider matched across both windows.
    articles_found: int
    recent: int
    #: ``None`` when the provider's page was full before reaching the earlier
    #: window, so the previous count is unknown rather than zero.
    previous: int | None
    top_articles: list[NewsArticle] = Field(default_factory=list)


class EvidenceCounts(BaseModel):
    """Literature sizing for one query. Counts, not papers."""

    source: str
    query: str
    total: int
    recent: int
    reviews_or_meta: int
    rcts: int


class PaperRef(BaseModel):
    pmid: str | None = None
    title: str
    year: int | None = None
    study_type: str


class DeepEvidence(BaseModel):
    """What the article pipeline itself would retrieve for this topic.

    Built from the pipeline's own extraction call and ``TemplateQueryStrategy``,
    so a topic that would die at ``UnanchoredQuery`` dies here instead, before
    a reviewer is asked to spend a run on it.
    """

    product: str
    ingredients: list[str]
    claims: list[str]
    queries: list[str]
    counts: EvidenceCounts
    top_papers: list[PaperRef] = Field(default_factory=list)


class ScienceSignal(BaseModel):
    """Stage 1 counts on the topic's phrase, and deep research if it got that far."""

    primary: EvidenceCounts | None = None
    #: The other provider's total for the same question, as a cross-check.
    cross_check: EvidenceCounts | None = None
    deep: DeepEvidence | None = None
    failures: list[str] = Field(default_factory=list)

    @property
    def best(self) -> EvidenceCounts | None:
        """Deep counts when present — they are the article's own query."""
        return self.deep.counts if self.deep is not None else self.primary


class NoveltySignal(BaseModel):
    max_similarity: float
    nearest: str | None = None


# --- candidates and state --------------------------------------------------


class Scores(BaseModel):
    """Component scores (0-100) that could be measured, and what could not."""

    components: dict[str, float] = Field(default_factory=dict)
    unavailable: list[str] = Field(default_factory=list)
    weights_used: dict[str, float] = Field(default_factory=dict)
    overall: float | None = None


class CandidateTopic(BaseModel):
    topic_id: str
    canonical_topic: str
    category: Category
    #: The substance, food or activity the topic is about ("creatine").
    subject: str
    #: What it is for ("sleep"). Empty for a bare-subject topic.
    outcome: str = ""
    reader_question: str | None = None
    queries: list[str]
    seeds: list[str] = Field(default_factory=list)
    trend: TrendSignal | None = None
    web: WebSignal | None = None
    news: NewsSignal | None = None
    science: ScienceSignal = Field(default_factory=ScienceSignal)
    novelty: NoveltySignal | None = None
    scores: Scores = Field(default_factory=Scores)
    evidence_status: EvidenceStatus | None = None
    status: ResearchCandidateStatus = ResearchCandidateStatus.CANDIDATE
    discard_reason: str | None = None
    rank: int | None = None
    in_web_pool: bool = False

    def discard(self, reason: str) -> None:
        self.status = ResearchCandidateStatus.DISCARDED
        self.discard_reason = reason

    @property
    def live(self) -> bool:
        return self.status is not ResearchCandidateStatus.DISCARDED

    def signals_json(self) -> dict[str, Any]:
        """What is stored in ``research_candidates.signals``.

        A discarded candidate keeps its counts and loses its examples (web
        results, news articles, top papers): the desk shows it only as a topic
        and a reason, and the examples were about two thirds of every row.
        """
        slim = self.status is ResearchCandidateStatus.DISCARDED
        drop_examples = {"top_results", "top_articles", "top_papers"} if slim else set()
        return {
            "subject": self.subject,
            "outcome": self.outcome,
            "reader_question": self.reader_question,
            "seeds": self.seeds,
            "trend": self.trend.model_dump(mode="json") if self.trend else None,
            "web": (
                self.web.model_dump(mode="json", exclude=drop_examples) if self.web else None
            ),
            "news": (
                self.news.model_dump(mode="json", exclude=drop_examples) if self.news else None
            ),
            "science": self.science.model_dump(
                mode="json",
                exclude={"deep": {"top_papers"}} if slim and self.science.deep else None,
            ),
            "novelty": self.novelty.model_dump(mode="json") if self.novelty else None,
            "in_web_pool": self.in_web_pool,
        }


class ProviderStatus(BaseModel):
    """``ok`` | ``partial`` (some calls failed) | ``failed`` | ``unavailable``
    (not configured). Recorded per provider per run."""

    status: str
    detail: str | None = None
    calls: int = 0
    failures: int = 0


class ResearchState(BaseModel):
    """Everything the graph threads. One key in LangGraph's state, replaced
    wholesale by every node — the same shape as ``pipeline/graph.py``."""

    run_id: str
    label: str
    mode: ResearchRunMode
    config: ResearchConfig
    stage: ResearchStage = ResearchStage.QUEUED
    today: date
    raw_trends: list[RawTrend] = Field(default_factory=list)
    candidates: list[CandidateTopic] = Field(default_factory=list)
    provider_status: dict[str, ProviderStatus] = Field(default_factory=dict)
    stage_log: list[dict[str, Any]] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    backfill_round: int = 0
    #: Set by a node that ends the run; the router sends the graph to END.
    failed: str | None = None

    def live(self) -> list[CandidateTopic]:
        return [candidate for candidate in self.candidates if candidate.live]

    def with_status(self, status: ResearchCandidateStatus) -> list[CandidateTopic]:
        return [candidate for candidate in self.candidates if candidate.status is status]

    def provider(self, name: str) -> ProviderStatus:
        return self.provider_status.setdefault(name, ProviderStatus(status="ok"))


class PastTopic(BaseModel):
    """Something already written, promoted or turned down, for the novelty check.

    ``kind`` is ``article`` (a pipeline run's topic), ``promoted`` or
    ``dismissed`` (research or discovery candidates a reviewer decided on).
    """

    text: str
    kind: str
    decided_at: datetime | None = None
