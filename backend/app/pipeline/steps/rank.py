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
from app.retrieval.rerank import RerankConfig, SemanticReranker, assign_handles

logger = logging.getLogger(__name__)


class RankStage:
    """Ranks what retrieval already cached. Reads only; writes nothing.

    It takes no ``SourceCache``, and that is the point of the shape rather than
    an omission. It used to re-upsert the entire candidate set purely to
    recover the row ids RetrieveStage had already learned — around a hundred
    extra statements per run, most of them single-row UPDATEs against rows that
    had been written seconds earlier. The ids ride along on
    ``ctx.candidates`` now (``CachedCandidate``), so the whole round trip is
    gone and this stage no longer has a reason to touch the cache at all.
    """

    name = StageName.RANK

    def __init__(self, reranker: SemanticReranker, config: RerankConfig) -> None:
        self._reranker = reranker
        self._config = config

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Rank candidates per claim, then assign citation handles globally."""
        if ctx.extraction is None:
            raise StageError(self.name, "extraction stage did not run")

        ranked_by_claim: dict[str, list[RankedSource]] = {}
        for claim, candidates in ctx.candidates.items():
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
