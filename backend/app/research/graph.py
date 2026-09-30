"""The research agent's LangGraph workflow.

```
load_config -> discover_trends -> expand_queries -> build_candidates
  -> check_novelty -> research_news -> research_science -> score_and_pool
  -> research_web -> shortlist -> deep_research -> select
     select --(fewer than the minimum, reserve left, round 0)--> backfill -> deep_research
     select --> END
```

Every edge also has a way out: a node that sets ``state.failed`` ends the run.
The one loop (backfill) runs at most ``MAX_BACKFILL_ROUNDS`` times.

A two-stage funnel, because the signals differ in cost by orders of magnitude:
Trends, news and PubMed counts are cheap and run on every candidate (up to
``max_candidates``); a web search is ~8 s and runs on the best
``web_pool_size``; deep research — the extraction call and the article
pipeline's own anchored query — runs on the ``shortlist_size`` best of those.

**Nothing in here generates an article or enqueues a run.** The last node
marks candidates ``selected``; a reviewer promoting one is what spends a
generation run. Same shape and same reason as ``pipeline/graph.py``: the graph
decides what runs next, and the one state key is replaced wholesale by each
node.
"""

from __future__ import annotations

import itertools
import logging
import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol, TypedDict

from langgraph.graph import END, StateGraph

from app.domain.contracts import ExtractionInput
from app.domain.enums import ResearchCandidateStatus
from app.llm.base import ExtractionClient, LLMError
from app.llm.embeddings.base import EmbeddingProvider
from app.research import normalize, scoring
from app.research.contracts import (
    CandidateTopic,
    DeepEvidence,
    EvidenceCounts,
    EvidenceStatus,
    NewsSignal,
    NoveltySignal,
    PaperRef,
    PastTopic,
    ProviderStatus,
    RawTrend,
    ResearchStage,
    ResearchState,
    TrendSignal,
)
from app.research.providers.base import (
    EvidenceQuery,
    NewsProvider,
    ProviderUnavailable,
    ScientificEvidenceProvider,
    TrendProvider,
    WebSearchProvider,
)
from app.research.providers.google_trends import BREAKOUT_PERCENT
from app.research.seeds import seeds_for
from app.research.triage import TriageClient, triage_or_fallback
from app.retrieval.base import ProviderError, ScholarlyProvider, SearchQuery
from app.retrieval.query_builder import (
    QueryStrategy,
    TemplateQueryStrategy,
    UnanchoredQuery,
)

logger = logging.getLogger(__name__)

#: Extra deep-research rounds when too few topics survive selection. One: the
#: reserve is the rest of the web pool, and a topic that did not make the first
#: shortlist rarely beats the ones that did.
MAX_BACKFILL_ROUNDS = 1
#: Claims per topic checked in deep research. The first is the topic's own
#: question; a second catches a topic whose first claim was phrased narrowly.
DEEP_CLAIMS = 2
#: Clusters handed to triage, as a multiple of ``max_candidates``: some will be
#: judged not to be wellness topics.
_TRIAGE_HEADROOM = 1.5
_TRENDS_BATCH = 4  # plus the anchor makes five, Google's limit
_WEB_RESULTS = 20


class TermsEvidence(ScientificEvidenceProvider, Protocol):
    """A science provider that can also count ready-made PubMed terms."""

    async def profile_terms(self, terms: str, recent_since: str) -> EvidenceCounts: ...


@dataclass
class ResearchDeps:
    """Everything the graph talks to. Any provider may be None (not configured)."""

    trends: TrendProvider | None
    web: WebSearchProvider | None
    news: NewsProvider | None
    #: Primary literature counts (PubMed) and the cross-check (Europe PMC).
    #: Each is the other's fallback.
    science: ScientificEvidenceProvider | None
    cross_check: ScientificEvidenceProvider | None
    #: PubMed counts for the article pipeline's own query terms.
    deep_counts: TermsEvidence | None
    #: PubMed search, for the top papers a reviewer is shown.
    papers: ScholarlyProvider | None
    extraction: ExtractionClient | None
    triage: TriageClient | None
    embedder: EmbeddingProvider | None
    history: Callable[[datetime], Awaitable[list[PastTopic]]]
    web_region: str = "us-en"
    query_strategy: QueryStrategy = field(default_factory=TemplateQueryStrategy)
    now: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))


Checkpoint = Callable[[ResearchState], Awaitable[None]]


class GraphState(TypedDict):
    state: ResearchState


NodeFn = Callable[[ResearchState], Awaitable[dict[str, Any]]]


class ResearchAgent:
    def __init__(self, deps: ResearchDeps, checkpoint: Checkpoint | None = None) -> None:
        self._deps = deps
        self._checkpoint = checkpoint
        self._graph = self._build()

    async def run(self, state: ResearchState) -> ResearchState:
        final = await self._graph.ainvoke(
            {"state": state}, config={"recursion_limit": recursion_limit()}
        )
        result: ResearchState = final["state"]
        if result.failed is None:
            result.stage = ResearchStage.COMPLETED
        return result

    # --- graph ---------------------------------------------------------------

    def _build(self) -> Any:
        steps: list[tuple[str, ResearchStage, NodeFn]] = [
            ("load_config", ResearchStage.LOADING_CONFIG, self.load_config),
            ("discover_trends", ResearchStage.DISCOVERING_TRENDS, self.discover_trends),
            ("expand_queries", ResearchStage.EXPANDING_TOPICS, self.expand_queries),
            ("build_candidates", ResearchStage.BUILDING_CANDIDATES, self.build_candidates),
            ("check_novelty", ResearchStage.CHECKING_NOVELTY, self.check_novelty),
            ("research_news", ResearchStage.RESEARCHING_NEWS, self.research_news),
            ("research_science", ResearchStage.RESEARCHING_SCIENCE, self.research_science),
            ("score_and_pool", ResearchStage.SCORING_TOPICS, self.score_and_pool),
            ("research_web", ResearchStage.SEARCHING_WEB, self.research_web),
            ("shortlist", ResearchStage.SHORTLISTING, self.shortlist),
            ("deep_research", ResearchStage.DEEP_RESEARCH, self.deep_research),
            ("select", ResearchStage.SELECTING_TOPICS, self.select),
            ("backfill", ResearchStage.BACKFILLING, self.backfill),
        ]
        graph: StateGraph = StateGraph(GraphState)
        for name, stage, fn in steps:
            graph.add_node(name, self._node(stage, fn))
        graph.set_entry_point("load_config")

        linear = [name for name, _, _ in steps[:-1]]  # through select
        for current, following in itertools.pairwise(linear):
            graph.add_conditional_edges(
                current, _unless_failed(following), {following: following, END: END}
            )
        graph.add_conditional_edges("select", _after_select, {"backfill": "backfill", END: END})
        graph.add_conditional_edges(
            "backfill",
            _unless_failed("deep_research"),
            {"deep_research": "deep_research", END: END},
        )
        return graph.compile()

    def _node(
        self, stage: ResearchStage, fn: NodeFn
    ) -> Callable[[GraphState], Awaitable[GraphState]]:
        async def node(graph_state: GraphState) -> GraphState:
            state = graph_state["state"]
            state.stage = stage
            if self._checkpoint is not None:
                # Written at the start as well as the end, so the desk shows
                # the step a run is *on* — some take minutes.
                await self._checkpoint(state)
            started = self._deps.now()
            clock = time.monotonic()
            metrics: dict[str, Any] = {}
            try:
                metrics = await fn(state)
            except Exception as exc:
                # Recorded, not re-raised: the run ends FAILED with the stage
                # and the error on it, and the candidates found so far are kept.
                logger.exception("research run %s failed at %s", state.label, stage)
                state.failed = f"{stage.value}: {type(exc).__name__}: {exc}"
            state.stage_log.append(
                {
                    "stage": stage.value,
                    "started_at": started.isoformat(),
                    "duration_ms": int((time.monotonic() - clock) * 1000),
                    "metrics": metrics,
                    **({"failed": state.failed} if state.failed else {}),
                }
            )
            if state.failed is not None:
                state.stage = ResearchStage.FAILED
            if self._checkpoint is not None:
                await self._checkpoint(state)
            return {"state": state}

        return node

    # --- nodes ---------------------------------------------------------------

    async def load_config(self, state: ResearchState) -> dict[str, Any]:
        deps = self._deps
        for name, provider in (
            ("google_trends", deps.trends),
            ("web_search", deps.web),
            ("news", deps.news),
            ("pubmed", deps.science),
            ("europe_pmc", deps.cross_check),
            ("triage", deps.triage),
            ("embeddings", deps.embedder),
            ("extraction", deps.extraction),
        ):
            state.provider_status[name] = ProviderStatus(
                status="ok" if provider is not None else "unavailable",
                detail=None if provider is not None else "not configured",
            )
        return {
            "categories": [c.value for c in state.config.categories],
            "target": state.config.target_article_count,
        }

    async def discover_trends(self, state: ResearchState) -> dict[str, Any]:
        trends = self._deps.trends
        if trends is None:
            state.failed = "no_trend_data: no trend provider is configured"
            return {}
        status = state.provider("google_trends")
        seeds = seeds_for(state.config.categories)
        errors: list[str] = []
        for seed, category in seeds:
            status.calls += 1
            try:
                related = await trends.related_queries(
                    seed, window_days=state.config.trend_window_days, geo=state.config.geo
                )
            except ProviderUnavailable as exc:
                status.failures += 1
                errors.append(str(exc))
                continue
            state.raw_trends.extend(_raw(related.rising, seed, category, trends.name, depth=0))

        _settle(status, errors)
        if not state.raw_trends:
            state.failed = "no_trend_data: " + (
                errors[-1] if errors else "Google Trends reported no rising queries"
            )
        return {
            "seeds": len(seeds),
            "rising_queries": len(state.raw_trends),
            "failed": len(errors),
        }

    async def expand_queries(self, state: ResearchState) -> dict[str, Any]:
        trends = self._deps.trends
        assert trends is not None  # discover_trends ends the run otherwise
        status = state.provider("google_trends")
        top = sorted(
            (t for t in state.raw_trends if t.depth == 0),
            key=normalize.trend_strength,
            reverse=True,
        )
        seen: set[str] = set()
        expanded = 0
        for trend in top:
            if len(seen) >= state.config.expand_top:
                break
            if trend.query in seen:
                continue
            seen.add(trend.query)
            status.calls += 1
            try:
                related = await trends.related_queries(
                    trend.query,
                    window_days=state.config.trend_window_days,
                    geo=state.config.geo,
                )
            except ProviderUnavailable as exc:
                status.failures += 1
                status.detail = str(exc)
                continue
            new = _raw(related.rising, trend.seed, trend.category, trends.name, depth=1)
            state.raw_trends.extend(new)
            expanded += len(new)
        _settle(status, [status.detail] if status.failures and status.detail else [])
        return {"expanded_from": len(seen), "new_queries": expanded}

    async def build_candidates(self, state: ResearchState) -> dict[str, Any]:
        config = state.config
        dropped_off_topic = normalize.off_topic(state.raw_trends)
        if dropped_off_topic:
            examples = ", ".join(sorted({t.query for t in dropped_off_topic})[:3])
            state.notes.append(
                f"{len(dropped_off_topic)} rising queries were not about health or their "
                f"seed and were dropped (e.g. {examples})"
            )
        clusters = normalize.merge_exact(state.raw_trends)
        exact = len(clusters)
        embedder = self._deps.embedder
        if embedder is not None and len(clusters) > 1:
            try:
                vectors = await embedder.embed_documents(
                    [cluster.representative.query for cluster in clusters]
                )
                clusters = normalize.merge_similar(clusters, vectors, config.cluster_threshold)
            except Exception as exc:  # an embedding outage costs merging, not the run
                state.provider("embeddings").status = "failed"
                state.provider("embeddings").detail = str(exc)
                state.notes.append(
                    f"similar queries were not merged: embeddings failed ({exc})"
                )
        clusters = clusters[: int(config.max_candidates * _TRIAGE_HEADROOM)]
        for index, cluster in enumerate(clusters):
            cluster.cluster_id = index

        candidates, note = await triage_or_fallback(
            self._deps.triage, clusters, allowed=config.categories
        )
        if note:
            state.notes.append(note)
            if self._deps.triage is not None:
                # Skipping some groups is partial; the call failing is failed.
                failed = note.startswith("triage failed")
                state.provider("triage").status = "failed" if failed else "partial"
                state.provider("triage").detail = note
        live = [c for c in candidates if c.live]
        dropped = live[config.max_candidates :]
        for candidate in dropped:
            candidate.discard("beyond the candidate cap")
        state.candidates = candidates

        await self._measure_interest(state)
        return {
            "off_topic_dropped": len(dropped_off_topic),
            "clusters_exact": exact,
            "clusters": len(clusters),
            "candidates": len(state.live()),
            "not_wellness_or_out_of_scope": sum(1 for c in candidates if not c.live)
            - len(dropped),
        }

    async def _measure_interest(self, state: ResearchState) -> None:
        """Trend signal per candidate: rising data, plus an anchored interest series."""
        trends = self._deps.trends
        assert trends is not None
        by_query: dict[str, RawTrend] = {}
        for trend in sorted(state.raw_trends, key=normalize.trend_strength, reverse=True):
            by_query.setdefault(trend.query, trend)

        live = state.live()
        for candidate in live:
            best = by_query.get(candidate.queries[0])
            candidate.trend = TrendSignal(
                source=trends.name,
                rising_percent=best.rising_percent if best else None,
                is_breakout=bool(best and best.is_breakout),
                growth_source="rising_percent" if best and best.rising_percent else None,
                related_queries=candidate.queries[1:6],
            )

        window = state.config.trend_window_days
        anchor = state.config.trends_anchor
        status = state.provider("google_trends")
        for start in range(0, len(live), _TRENDS_BATCH):
            batch = live[start : start + _TRENDS_BATCH]
            queries = [c.queries[0] for c in batch]
            status.calls += 1
            try:
                series = await trends.interest_over_time(
                    [*queries, anchor], days=2 * window, geo=state.config.geo
                )
            except ProviderUnavailable as exc:
                status.failures += 1
                status.detail = str(exc)
                continue
            anchor_now, _ = _windows(series.get(anchor, []), window)
            for candidate, query in zip(batch, queries, strict=True):
                current, previous = _windows(series.get(query, []), window)
                if current is None or candidate.trend is None:
                    continue
                candidate.trend.current_interest = current
                candidate.trend.previous_interest = previous
                candidate.trend.anchor_interest = anchor_now
                if previous is not None and current + previous > 0:
                    candidate.trend.growth_source = "interest_series"
                    candidate.trend.growth_percent = scoring.growth_percent(current, previous)
        _settle(status, [status.detail] if status.failures and status.detail else [])

    async def check_novelty(self, state: ResearchState) -> dict[str, Any]:
        embedder = self._deps.embedder
        live = state.live()
        if embedder is None or not live:
            return {"checked": 0}
        since = self._deps.now() - timedelta(days=state.config.novelty_lookback_days)
        history = await self._deps.history(since)
        if not history:
            for candidate in live:
                candidate.novelty = NoveltySignal(max_similarity=0.0)
            return {"checked": len(live), "history": 0, "duplicates": 0}
        try:
            vectors = await embedder.embed_documents(
                [c.canonical_topic for c in live] + [past.text for past in history]
            )
        except Exception as exc:
            state.provider("embeddings").status = "failed"
            state.provider("embeddings").detail = str(exc)
            state.notes.append(f"novelty was not measured: embeddings failed ({exc})")
            return {"checked": 0}
        own, past = vectors[: len(live)], vectors[len(live) :]
        duplicates = 0
        for candidate, vector in zip(live, own, strict=True):
            similarities = [normalize.cosine(vector, other) for other in past]
            best = max(range(len(history)), key=lambda i: similarities[i])
            candidate.novelty = NoveltySignal(
                max_similarity=round(similarities[best], 4), nearest=history[best].text
            )
            if similarities[best] >= state.config.duplicate_threshold:
                duplicates += 1
                item = history[best]
                when = f" on {item.decided_at.date()}" if item.decided_at else ""
                candidate.discard(f"already {_KIND_VERB[item.kind]}{when}: {item.text!r}")
        return {"checked": len(live), "history": len(history), "duplicates": duplicates}

    async def research_news(self, state: ResearchState) -> dict[str, Any]:
        news = self._deps.news
        if news is None:
            return {"searched": 0}
        status = state.provider("news")
        today = state.today
        window = state.config.news_window_days
        since, boundary = today - timedelta(days=2 * window), today - timedelta(days=window)
        found = 0
        for candidate in state.live():
            status.calls += 1
            terms = [candidate.subject] + ([candidate.outcome] if candidate.outcome else [])
            try:
                results = await news.search(
                    terms, since=since, until=today, language=state.config.language
                )
            except ProviderUnavailable as exc:
                status.failures += 1
                status.detail = str(exc)
                continue
            candidate.news = news_signal(
                results.source, results.total, results.articles, boundary
            )
            found += 1
        _settle(status, [status.detail] if status.failures and status.detail else [])
        return {"searched": found, "failed": status.failures}

    async def research_science(self, state: ResearchState) -> dict[str, Any]:
        primary, cross = self._deps.science, self._deps.cross_check
        recent_since = state.today - timedelta(days=state.config.scientific_window_days)
        pubmed, epmc = state.provider("pubmed"), state.provider("europe_pmc")
        checked = 0
        for candidate in state.live():
            query = EvidenceQuery(
                subject=candidate.subject, outcome=candidate.outcome, recent_since=recent_since
            )
            try:
                main = await _profile(primary, query, pubmed, candidate)
                other = await _profile(cross, query, epmc, candidate)
            except ValueError:
                candidate.discard(f"no searchable subject in {candidate.subject!r}")
                continue
            # Each is the other's fallback: an unchecked topic needs both down.
            candidate.science.primary = main or other
            candidate.science.cross_check = other if main is not None else None
            candidate.evidence_status = scoring.evidence_status(candidate.science.best)
            if candidate.science.primary is not None:
                checked += 1
        for status in (pubmed, epmc):
            _settle(status, [status.detail] if status.failures and status.detail else [])
        if state.live() and checked == 0:
            state.failed = (
                "scientific_evidence_unavailable: neither PubMed nor Europe PMC could "
                "be reached, so no topic's evidence was checked"
            )
        return {"checked": checked, "unchecked": len(state.live()) - checked}

    async def score_and_pool(self, state: ResearchState) -> dict[str, Any]:
        config = state.config
        for candidate in state.live():
            if candidate.evidence_status is EvidenceStatus.NONE:
                candidate.discard("no literature found for this topic in PubMed or Europe PMC")
        self._rescore(state)
        ranked = scoring.rank(state.candidates)
        for candidate in ranked[: config.web_pool_size]:
            candidate.in_web_pool = True
        return {"scored": len(ranked), "web_pool": min(len(ranked), config.web_pool_size)}

    async def research_web(self, state: ResearchState) -> dict[str, Any]:
        web = self._deps.web
        if web is None:
            return {"searched": 0}
        status = state.provider("web_search")
        searched = 0
        for candidate in state.live():
            if not candidate.in_web_pool:
                continue
            status.calls += 1
            try:
                results = await web.search(
                    candidate.canonical_topic,
                    region=self._deps.web_region,
                    max_results=_WEB_RESULTS,
                )
            except ProviderUnavailable as exc:
                status.failures += 1
                status.detail = str(exc)
                continue
            candidate.web = scoring.web_signal(
                results,
                source=web.name,
                subject=candidate.subject,
                authoritative=state.config.authoritative_domains,
            )
            searched += 1
        _settle(status, [status.detail] if status.failures and status.detail else [])
        return {"searched": searched, "failed": status.failures}

    async def shortlist(self, state: ResearchState) -> dict[str, Any]:
        config = state.config
        self._rescore(state)
        pool = [c for c in scoring.rank(state.candidates) if c.in_web_pool]
        thin = 0
        for candidate in pool:
            sources = scoring.content_sources(candidate)
            if sources is not None and sources < config.min_sources:
                candidate.discard(
                    f"too little material to research ({sources} web/news sources; "
                    f"needs {config.min_sources})"
                )
                thin += 1
        chosen = [c for c in pool if c.live][: config.shortlist_size]
        for candidate in chosen:
            candidate.status = ResearchCandidateStatus.SHORTLISTED
        return {"shortlisted": len(chosen), "too_little_material": thin}

    async def deep_research(self, state: ResearchState) -> dict[str, Any]:
        done = unanchored = failed = 0
        for candidate in state.with_status(ResearchCandidateStatus.SHORTLISTED):
            if candidate.science.deep is not None:
                continue
            outcome = await self._deep(state, candidate)
            if outcome == "unanchored":
                unanchored += 1
            elif outcome == "failed":
                failed += 1
            else:
                done += 1
        return {"researched": done, "unanchored": unanchored, "failed": failed}

    async def _deep(self, state: ResearchState, candidate: CandidateTopic) -> str:
        """Ask what the article pipeline would ask, the way it would ask it.

        Extraction on the canonical topic, then ``TemplateQueryStrategy`` for the
        first claims — the same calls RETRIEVE makes — counted in PubMed. A topic
        whose query cannot be anchored is discarded here: promoted, it would fail
        at RETRIEVE as a non-retryable ``UnanchoredQuery`` having spent a model
        call, which is the pipeline's sharpest documented risk.
        """
        deps = self._deps
        if deps.extraction is None or deps.deep_counts is None:
            return "failed"
        try:
            extracted = (
                await deps.extraction.extract(ExtractionInput(topic=candidate.canonical_topic))
            ).output
        except LLMError as exc:
            candidate.science.failures.append(f"extraction: {exc}")
            return "failed"

        claims = extracted.target_claims[:DEEP_CLAIMS]
        try:
            built = [
                deps.query_strategy.build(claim, extracted.product, extracted.ingredients)
                for claim in claims
            ]
        except UnanchoredQuery:
            candidate.discard(
                "the article pipeline could not anchor a literature query "
                f"(product {extracted.product!r}, no searchable ingredient)"
            )
            return "unanchored"

        recent_since = (
            state.today - timedelta(days=state.config.scientific_window_days)
        ).isoformat()
        counts: list[EvidenceCounts] = []
        terms_used: list[str] = []
        for queries in built:
            terms = queries[-1].terms  # the general pass; the review pass shares its terms
            terms_used.append(terms)
            try:
                counts.append(await deps.deep_counts.profile_terms(terms, recent_since))
            except ProviderUnavailable as exc:
                candidate.science.failures.append(f"deep counts: {exc}")
        if not counts:
            return "failed"

        papers: list[PaperRef] = []
        if deps.papers is not None and built:
            try:
                found = await deps.papers.search(
                    SearchQuery(
                        claim=claims[0], terms=terms_used[0], reviews_only=True, max_results=5
                    )
                )
                papers = [
                    PaperRef(
                        pmid=p.pmid, title=p.title, year=p.year, study_type=str(p.study_type)
                    )
                    for p in found
                ]
            except ProviderError as exc:
                candidate.science.failures.append(f"top papers: {exc}")

        # The best-evidenced claim sizes the topic. The article's verdict is
        # still capped per claim at VALIDATE; this only decides what to propose.
        best = max(counts, key=lambda c: (scoring.evidence_score(c) or 0.0, c.total))
        candidate.science.deep = DeepEvidence(
            product=extracted.product,
            ingredients=extracted.ingredients,
            claims=claims,
            queries=terms_used,
            counts=best,
            top_papers=papers,
        )
        candidate.evidence_status = scoring.evidence_status(best)
        return "done"

    async def select(self, state: ResearchState) -> dict[str, Any]:
        config = state.config
        for candidate in state.with_status(ResearchCandidateStatus.SELECTED):
            candidate.status = ResearchCandidateStatus.SHORTLISTED
        self._rescore(state)

        gated = 0
        for candidate in state.with_status(ResearchCandidateStatus.SHORTLISTED):
            reason = scoring.gate_reason(
                candidate,
                min_status=config.min_evidence_status,
                min_sources=config.min_sources,
            )
            if reason is not None:
                candidate.discard(reason)
                gated += 1

        ranked = scoring.rank(state.with_status(ResearchCandidateStatus.SHORTLISTED))
        chosen = scoring.select(ranked, target=config.target_article_count)
        for candidate in chosen:
            candidate.status = ResearchCandidateStatus.SELECTED
        for position, candidate in enumerate(scoring.rank(state.candidates), start=1):
            candidate.rank = position

        backfilling = (
            len(chosen) < config.min_article_count
            and state.backfill_round < MAX_BACKFILL_ROUNDS
            and bool(self._reserve(state))
        )
        if len(chosen) < config.target_article_count and not backfilling:
            state.notes.append(
                f"selected {len(chosen)} of {config.target_article_count} requested: "
                "no other candidate passed the evidence and material checks"
            )
        return {"selected": len(chosen), "discarded_by_gate": gated}

    async def backfill(self, state: ResearchState) -> dict[str, Any]:
        config = state.config
        state.backfill_round += 1
        missing = config.target_article_count - len(
            state.with_status(ResearchCandidateStatus.SELECTED)
        )
        reserve = self._reserve(state)[: max(2, 2 * missing)]
        for candidate in reserve:
            candidate.status = ResearchCandidateStatus.SHORTLISTED
        return {"round": state.backfill_round, "added": len(reserve)}

    # --- helpers -------------------------------------------------------------

    def _rescore(self, state: ResearchState) -> None:
        for candidate in state.live():
            candidate.scores = scoring.score(
                candidate,
                state.config.weights,
                duplicate_threshold=state.config.duplicate_threshold,
            )

    @staticmethod
    def _reserve(state: ResearchState) -> list[CandidateTopic]:
        """Web-pool candidates never shortlisted: the backfill round's supply."""
        return [
            c
            for c in scoring.rank(state.with_status(ResearchCandidateStatus.CANDIDATE))
            if c.in_web_pool
        ]


_KIND_VERB = {"article": "written about", "promoted": "promoted", "dismissed": "dismissed"}


def _unless_failed(following: str) -> Callable[[GraphState], str]:
    def route(graph_state: GraphState) -> str:
        return END if graph_state["state"].failed is not None else following

    return route


def _after_select(graph_state: GraphState) -> str:
    state = graph_state["state"]
    selected = len(state.with_status(ResearchCandidateStatus.SELECTED))
    if (
        state.failed is None
        and selected < state.config.min_article_count
        and state.backfill_round < MAX_BACKFILL_ROUNDS
        and ResearchAgent._reserve(state)
    ):
        return "backfill"
    return END


def recursion_limit() -> int:
    """Thirteen nodes plus the backfill loop (backfill, deep_research, select)."""
    return 13 + MAX_BACKFILL_ROUNDS * 3 + 2


def _raw(
    rows: list[Any], seed: str, category: Any, source: str, *, depth: int
) -> list[RawTrend]:
    return [
        RawTrend(
            query=row.query,
            seed=seed,
            category=category,
            source=source,
            rising_percent=row.value,
            is_breakout=row.value >= BREAKOUT_PERCENT,
            depth=depth,
        )
        for row in rows
    ]


def _windows(values: list[float], window: int) -> tuple[float | None, float | None]:
    """Means of the last ``window`` days and the ``window`` days before them."""
    if len(values) < window:
        return None, None
    current = statistics.fmean(values[-window:])
    earlier = values[-2 * window : -window]
    return round(current, 2), round(statistics.fmean(earlier), 2) if earlier else None


def news_signal(source: str, total: int, articles: list[Any], boundary: date) -> NewsSignal:
    """Split articles at the window boundary.

    When the provider's page ran out before reaching the earlier window, the
    earlier count is unknown — reported as ``None``, never as zero, which would
    read as a surge.
    """
    recent = [
        a for a in articles if a.published_at is None or a.published_at.date() >= boundary
    ]
    earlier = len(articles) - len(recent)
    truncated = total > len(articles)
    previous: int | None
    if not truncated:
        previous = earlier
    elif earlier > 0:
        # The page reached the earlier window, so the recent count is complete
        # and the provider's total accounts for the rest.
        previous = total - len(recent)
    else:
        previous = None
    return NewsSignal(
        source=source,
        articles_found=total,
        recent=len(recent),
        previous=previous,
        top_articles=articles[:5],
    )


async def _profile(
    provider: ScientificEvidenceProvider | None,
    query: EvidenceQuery,
    status: ProviderStatus,
    candidate: CandidateTopic,
) -> EvidenceCounts | None:
    if provider is None:
        return None
    status.calls += 1
    try:
        return await provider.profile(query)
    except ProviderUnavailable as exc:
        status.failures += 1
        status.detail = str(exc)
        candidate.science.failures.append(str(exc))
        return None


def _settle(status: ProviderStatus, errors: list[str]) -> None:
    """ok / partial / failed from the call counts, with the last error kept."""
    if status.status == "unavailable":
        return
    if status.calls and status.failures >= status.calls:
        status.status = "failed"
    elif status.failures:
        status.status = "partial"
    else:
        status.status = "ok"
    if errors:
        status.detail = errors[-1]
