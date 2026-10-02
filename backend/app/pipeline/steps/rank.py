"""Stage 3 — rank each claim's candidates, then number the sources S1..Sn.

Reads what RetrieveStage cached and writes nothing. The scoring itself is in
``retrieval/rerank.py``.
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy.exc import InterfaceError, OperationalError

from app.domain.contracts import RankedSource
from app.domain.enums import Verdict
from app.evidence.grading import QUORUM_FOR_SUPPORTED, VERDICT_CEILING
from app.llm.embeddings.base import EmbeddingError
from app.pipeline.stages import PipelineContext, StageError, StageName
from app.retrieval.rerank import (
    RerankConfig,
    SemanticReranker,
    assign_handles,
    claim_subject,
)

logger = logging.getLogger(__name__)


def thin_claims(ranked: dict[str, list[RankedSource]]) -> list[str]:
    """Claims with fewer than ``QUORUM_FOR_SUPPORTED`` supported-tier sources.

    Such a claim cannot reach ``supported`` however well the article is
    written, so the graph sends it back through RETRIEVE with a broader query.
    This counts *strong* sources, not sources: a claim with twelve weak
    studies is thin, one with three good reviews is not.
    """
    return sorted(
        claim
        for claim, entries in ranked.items()
        if sum(
            1
            for entry in entries
            if VERDICT_CEILING[entry.paper.study_type] is Verdict.SUPPORTED
        )
        < QUORUM_FOR_SUPPORTED
    )


class RankStage:
    name = StageName.RANK

    def __init__(self, reranker: SemanticReranker, config: RerankConfig) -> None:
        self._reranker = reranker
        self._config = config

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Rank candidates per claim, then assign citation handles across the article."""
        if ctx.extraction is None:
            raise StageError(self.name, "extraction stage did not run")

        subject = claim_subject(ctx.extraction.product, ctx.extraction.ingredients)
        ranked_by_claim: dict[str, list[RankedSource]] = {}
        try:
            for claim, candidates in ctx.candidates.items():
                ranked_by_claim[claim] = await self._reranker.rank_for_claim(
                    claim, candidates, self._config, subject=subject
                )
        except (EmbeddingError, OperationalError, InterfaceError) as exc:
            # The embedding server or the database being unreachable is an
            # outage: retrying finds every paper already cached and embedded.
            raise StageError(self.name, f"ranking failed: {exc}", retryable=True) from exc
        ranked_by_claim = assign_handles(ranked_by_claim)

        thin = thin_claims(ranked_by_claim)
        if thin:
            logger.info(
                "%d of %d claims are below the supported-tier quorum: %s",
                len(thin),
                len(ranked_by_claim),
                ", ".join(repr(claim) for claim in thin),
            )

        all_ranked = [entry for entries in ranked_by_claim.values() for entry in entries]
        study_types = Counter(str(entry.paper.study_type) for entry in all_ranked)
        ctx.record_metrics(
            self.name,
            {
                "kept_per_claim": {
                    claim: len(entries) for claim, entries in ranked_by_claim.items()
                },
                # All in-vitro here means the verdict will be capped at 'weak'.
                "study_types": dict(study_types),
                "unique_sources": len({entry.source_id for entry in all_ranked}),
                "refine_round": ctx.refine_round,
                "thin_claims": thin,
                "ranked_against": subject,
            },
        )

        return ctx.model_copy(update={"ranked": ranked_by_claim, "thin_claims": thin})
