"""Stage 3 — semantic re-rank and evidence-grade filtering.

Pass 2 of hybrid retrieval. This is where pgvector earns its place: cosine
similarity against the specific claim, restricted to this run's candidates,
combined with the grade and recency bonuses from retrieval/rerank.py.
"""

from __future__ import annotations

import logging
from collections import Counter

from app.domain.contracts import RankedSource
from app.pipeline.stages import PipelineContext, StageError, StageName
from app.retrieval.cache import CachedSource, SourceCache
from app.retrieval.rerank import RerankConfig, SemanticReranker, assign_handles

logger = logging.getLogger(__name__)


class RankStage:
    name = StageName.RANK

    def __init__(
        self,
        reranker: SemanticReranker,
        cache: SourceCache,
        config: RerankConfig,
    ) -> None:
        self._reranker = reranker
        self._cache = cache
        self._config = config

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Rank candidates per claim, then assign citation handles globally."""
        if ctx.extraction is None:
            raise StageError(self.name, "extraction stage did not run")

        # Re-resolve candidates to their cached rows so ranking has source ids.
        cached_by_key: dict[str, CachedSource] = {}
        all_papers = [paper for papers in ctx.candidates.values() for paper in papers]
        if all_papers:
            for entry in await self._cache.upsert_many(all_papers):
                cached_by_key[entry.paper.dedup_key] = entry

        ranked_by_claim: dict[str, list[RankedSource]] = {}
        for claim, papers in ctx.candidates.items():
            candidates = [
                cached_by_key[paper.dedup_key]
                for paper in papers
                if paper.dedup_key in cached_by_key
            ]
            ranked_by_claim[claim] = await self._reranker.rank_for_claim(
                claim, candidates, self._config
            )

        # Handles are assigned once, globally, across the whole article — two
        # claims sharing a source must give it the same handle, or the model
        # sees one paper twice under different names and cites it as if it were
        # two independent findings.
        ranked_by_claim = assign_handles(ranked_by_claim)

        study_types = Counter(
            str(entry.paper.study_type)
            for entries in ranked_by_claim.values()
            for entry in entries
        )

        # An all-in-vitro kept set means the verdict is about to be capped at
        # 'weak'. Far easier to understand here than to reverse-engineer from a
        # validation failure two stages later.
        ctx.record_metrics(
            self.name,
            {
                "kept_per_claim": {
                    claim: len(entries) for claim, entries in ranked_by_claim.items()
                },
                "study_types": dict(study_types),
                "unique_sources": len(
                    {
                        entry.source_id
                        for entries in ranked_by_claim.values()
                        for entry in entries
                    }
                ),
            },
        )

        return ctx.model_copy(update={"ranked": ranked_by_claim})
