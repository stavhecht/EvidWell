"""Stage 2 — keyword recall against the scholarly APIs.

Pass 1 of the hybrid retrieval described in DESIGN.md §4. Fan out across
providers per claim, normalise, dedup, cache.
"""

from __future__ import annotations

import asyncio
import logging

from app.domain.contracts import CandidatePaper
from app.domain.enums import EVIDENCE_RANK
from app.pipeline.stages import PipelineContext, StageError, StageName
from app.retrieval.base import ScholarlyProvider
from app.retrieval.cache import SourceCache
from app.retrieval.query_builder import QueryStrategy

logger = logging.getLogger(__name__)


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
        """Query every provider for every claim; dedup and cache the results.

        Partial provider failure is tolerated: one API being down degrades
        recall, and degraded recall produces a more cautious verdict, which is
        an acceptable outcome. A stage error is raised only if *every* provider
        fails for *every* claim — that is a systems problem, not an evidence
        one.

        **Zero candidates is a valid result, not an error.** A fringe trend with
        no literature must reach synthesis so the article can honestly say "no
        evidence". Raising here would turn the most important thing this product
        can say into a crash.
        """
        if ctx.extraction is None:
            raise StageError(self.name, "extraction stage did not run")

        candidates: dict[str, list[CandidatePaper]] = {}
        provider_hits: dict[str, int] = {}
        failures = 0
        attempts = 0

        for claim in ctx.extraction.target_claims:
            queries = self._strategy.build(claim, ctx.extraction.ingredients)

            tasks = [
                provider.search(query)
                for query in queries
                for provider in self._providers
            ]
            attempts += len(tasks)
            results = await asyncio.gather(*tasks, return_exceptions=True)

            collected: list[CandidatePaper] = []
            for index, result in enumerate(results):
                provider = self._providers[index % len(self._providers)]
                if isinstance(result, BaseException):
                    failures += 1
                    logger.warning(
                        "provider %s failed for claim %r: %s",
                        provider.source_api,
                        claim,
                        result,
                    )
                    continue
                collected.extend(result)
                provider_hits[provider.source_api] = provider_hits.get(
                    provider.source_api, 0
                ) + len(result)

            deduped = self._dedup(collected)[: self._max_candidates]
            candidates[claim] = deduped
            logger.info(
                "claim %r: %d raw -> %d deduped candidates",
                claim,
                len(collected),
                len(deduped),
            )

        if attempts and failures == attempts:
            raise StageError(
                self.name,
                f"every provider call failed ({failures}/{attempts})",
                retryable=True,
            )

        # Upsert first, embed second — see cache.py. The reverse re-embeds the
        # whole candidate set on every run.
        all_papers = [paper for papers in candidates.values() for paper in papers]
        unique = self._dedup(all_papers)
        cached = await self._cache.upsert_many(unique)
        await self._cache.ensure_embeddings(cached)

        ctx.record_metrics(
            self.name,
            {
                "provider_hits": provider_hits,
                "candidates_total": len(all_papers),
                "candidates_unique": len(unique),
                "provider_failures": failures,
                "cache_hits": sum(1 for entry in cached if entry.had_embedding),
            },
        )

        return ctx.model_copy(update={"candidates": candidates})

    @staticmethod
    def _dedup(papers: list[CandidatePaper]) -> list[CandidatePaper]:
        """Collapse cross-provider duplicates, merging what each knew.

        Keeps the longest abstract, the highest citation count, and the
        strongest study-type classification across the group. Strongest-wins on
        study type is intentional: one provider knowing a paper is an RCT is
        more informative than another not knowing.
        """
        merged: dict[str, CandidatePaper] = {}
        for paper in papers:
            key = paper.dedup_key
            existing = merged.get(key)
            if existing is None:
                merged[key] = paper
                continue

            merged[key] = existing.model_copy(
                update={
                    "pmid": existing.pmid or paper.pmid,
                    "doi": existing.doi or paper.doi,
                    "url": existing.url or paper.url,
                    "journal": existing.journal or paper.journal,
                    "year": existing.year or paper.year,
                    "abstract": max(
                        existing.abstract, paper.abstract, key=len
                    ),
                    "citation_count": max(
                        existing.citation_count or 0, paper.citation_count or 0
                    )
                    or None,
                    "study_type": max(
                        existing.study_type,
                        paper.study_type,
                        key=lambda study: EVIDENCE_RANK[study],
                    ),
                    "raw_study_type": existing.raw_study_type or paper.raw_study_type,
                }
            )
        return list(merged.values())
