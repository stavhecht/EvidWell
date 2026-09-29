"""Stage 2 — search the scholarly APIs for every claim and cache the results.

For each claim:
  1. Build the queries (``retrieval/query_builder.py``).
  2. Run every query against every provider in parallel.
  3. Drop trial protocols and retracted papers.
Then, across all claims:
  4. Merge duplicate papers (``retrieval/dedup.py``).
  5. Save them to the cache and embed new abstracts in chunks
     (``retrieval/cache.py``).

Three outcomes for a claim, deliberately kept apart:

* **Searched, found nothing.** Fine. The article can honestly say "no evidence".
* **Every provider call failed.** The stage fails (retryable). Otherwise a
  throttled search would reach synthesis as the same empty list as a real
  "nothing found", and publish a confident verdict nobody checked.
* **The query names no substance.** The stage fails (not retryable). An
  outcome-only query returns real but off-topic papers, and every later
  stage would read that as success.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from app.domain.contracts import CachedCandidate, CandidatePaper, ExtractionOutput
from app.domain.enums import SourceApi
from app.evidence.grading import is_protocol, is_retracted
from app.pipeline.stages import PipelineContext, StageError, StageName
from app.retrieval.base import RateLimited, ScholarlyProvider, SearchQuery
from app.retrieval.cache import CachedSource, SourceCache
from app.retrieval.dedup import merge_candidates, unique_papers
from app.retrieval.query_builder import QueryStrategy, UnanchoredQuery

logger = logging.getLogger(__name__)

ComposeQueries = Callable[[str, str, list[str]], list[SearchQuery]]


@dataclass
class _SearchStats:
    """Counters collected while searching, reported as stage metrics."""

    provider_hits: Counter[SourceApi] = field(default_factory=Counter)
    failures: int = 0
    rate_limited: int = 0
    protocols_dropped: int = 0
    retractions_dropped: int = 0


class RetrieveStage:
    name = StageName.RETRIEVE

    def __init__(
        self,
        providers: list[ScholarlyProvider],
        strategy: QueryStrategy,
        cache: SourceCache,
        max_candidates_per_claim: int = 50,
    ) -> None:
        self._providers = providers
        self._strategy = strategy
        self._cache = cache
        self._max_candidates = max_candidates_per_claim

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        if ctx.extraction is None:
            raise StageError(self.name, "extraction stage did not run")

        # A refinement pass re-searches only the claims RANK found thin, with a
        # broader query (the outcome dropped, the substance kept).
        refining = ctx.refine_round > 0
        claims = list(ctx.thin_claims) if refining else list(ctx.extraction.target_claims)
        compose = self._strategy.broaden if refining else self._strategy.build

        # Steps 1–3, per claim.
        stats = _SearchStats()
        found: dict[str, list[CandidatePaper]] = {}
        unsearched: list[str] = []
        for claim in claims:
            queries = self._build_queries(compose, claim, ctx.extraction)
            found[claim], all_failed = await self._search(claim, queries, stats)
            if all_failed:
                unsearched.append(claim)

        if unsearched:
            detail = f"; {stats.rate_limited} rate limited" if stats.rate_limited else ""
            raise StageError(
                self.name,
                f"{len(unsearched)} of {len(found)} claims went unsearched "
                f"(every provider call failed{detail}): "
                f"{', '.join(repr(claim) for claim in unsearched)}",
                retryable=True,  # usually a throttle that has since cleared
            )

        # Step 4. Across all claims at once, so a paper answering two claims
        # is the same merged record in both lists.
        candidates = {
            claim: papers[: self._max_candidates]
            for claim, papers in merge_candidates(found).items()
        }
        for claim, papers in candidates.items():
            logger.info(
                "claim %r: %d raw -> %d deduped candidates",
                claim,
                len(found[claim]),
                len(papers),
            )

        # Step 5.
        unique = unique_papers(candidates)
        cached = await self._cache.upsert_many(unique)
        await self._cache.ensure_embeddings(cached)
        paired = self._pair_with_ids(candidates, cached)

        # A refinement pass keeps every claim the first pass already answered.
        if refining:
            paired = {**ctx.candidates, **paired}

        raw_total = sum(len(papers) for papers in found.values())
        ctx.record_metrics(
            self.name,
            {
                "provider_hits": dict(stats.provider_hits),
                "candidates_total": raw_total,
                "protocols_dropped": stats.protocols_dropped,
                "retractions_dropped": stats.retractions_dropped,
                "candidates_unique": len(unique),
                # Near zero with several providers on means dedup stopped working.
                "duplicates_merged": raw_total - sum(len(p) for p in candidates.values()),
                "provider_failures": stats.failures,
                # Separate from failures: throttling wants slower pacing,
                # failure wants a look at the provider.
                "rate_limited": stats.rate_limited,
                "cache_hits": sum(1 for entry in cached if entry.had_embedding),
                "refine_round": ctx.refine_round,
                "claims_searched": len(claims),
            },
        )
        return ctx.model_copy(update={"candidates": paired})

    def _build_queries(
        self, compose: ComposeQueries, claim: str, extraction: ExtractionOutput
    ) -> list[SearchQuery]:
        """Step 1. A query that names no substance fails the stage for good."""
        try:
            return compose(claim, extraction.product, extraction.ingredients)
        except UnanchoredQuery as exc:
            # Not retryable: the same extraction would build the same query.
            raise StageError(self.name, str(exc), retryable=False) from exc

    async def _search(
        self, claim: str, queries: list[SearchQuery], stats: _SearchStats
    ) -> tuple[list[CandidatePaper], bool]:
        """Steps 2–3: every query against every provider, in parallel.

        Returns the usable papers, and whether every call failed. Some calls
        failing is tolerated; it only lowers recall.
        """
        calls = [(provider, query) for query in queries for provider in self._providers]
        results = await asyncio.gather(
            *(provider.search(query) for provider, query in calls), return_exceptions=True
        )

        papers: list[CandidatePaper] = []
        failed = 0
        for (provider, _), result in zip(calls, results, strict=True):
            if isinstance(result, BaseException):
                failed += 1
                if isinstance(result, RateLimited):
                    stats.rate_limited += 1
                logger.warning(
                    "provider %s failed for claim %r: %s", provider.source_api, claim, result
                )
                continue
            usable = self._screen(result, stats)
            papers.extend(usable)
            stats.provider_hits[provider.source_api] += len(usable)

        stats.failures += failed
        return papers, bool(calls) and failed == len(calls)

    @staticmethod
    def _screen(papers: list[CandidatePaper], stats: _SearchStats) -> list[CandidatePaper]:
        """Step 3. Drop trial protocols and retracted papers before they are cached.

        A protocol is a plan and reports no findings; a retracted paper reports
        findings the literature has withdrawn. Retractions issued *after* a
        paper was cached are caught by ``scripts/check_retractions.py``.
        """
        kept: list[CandidatePaper] = []
        for paper in papers:
            if is_protocol(paper.raw_study_type, paper.title):
                stats.protocols_dropped += 1
            elif is_retracted(paper.raw_study_type):
                stats.retractions_dropped += 1
                logger.info(
                    "refused retracted paper pmid=%s doi=%s: %r",
                    paper.pmid,
                    paper.doi,
                    paper.title,
                )
            else:
                kept.append(paper)
        return kept

    def _pair_with_ids(
        self, candidates: dict[str, list[CandidatePaper]], cached: list[CachedSource]
    ) -> dict[str, list[CachedCandidate]]:
        """Attach each candidate's ``sources`` row id, which RankStage needs.

        A missing id raises rather than dropping the paper: a paper lost here
        would silently read as thinner evidence downstream.
        """
        source_ids = {entry.paper.dedup_key: entry.source_id for entry in cached}
        try:
            return {
                claim: [
                    CachedCandidate(source_id=source_ids[paper.dedup_key], paper=paper)
                    for paper in papers
                ]
                for claim, papers in candidates.items()
            }
        except KeyError as exc:
            raise StageError(
                self.name, f"source cache returned no row for candidate {exc}"
            ) from exc
