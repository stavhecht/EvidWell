"""Pass 2 — semantic re-rank against a specific claim, then grade filtering.

The scoring is deliberately not pure cosine similarity. Similarity answers "is
this paper about the claim?"; it says nothing about whether the paper is any
good. A cell-culture study whose abstract uses the claim's exact vocabulary
will out-similarity a systematic review that phrases things clinically — and
that is precisely the ranking we must not produce.

    score = cosine_similarity + grade_bonus + recency_bonus

**The bonus magnitudes below are a starting point to be tuned against real
output, not a claim of correctness.** They are module constants so tuning is a
one-line change with a visible diff. The intent, per the brief, is that a
single recent systematic review outranks several topically-tighter primary
studies.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Float, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import CandidatePaper, RankedSource
from app.domain.enums import EVIDENCE_RANK, StudyType
from app.domain.models import Source
from app.llm.embeddings.base import EmbeddingProvider
from app.retrieval.cache import CachedSource

logger = logging.getLogger(__name__)

#: Additive bonus by study type. The spread between META_ANALYSIS and IN_VITRO
#: (0.25) is wide enough to reorder across a realistic similarity gap, which is
#: the point — see module docstring.
GRADE_BONUS: dict[StudyType, float] = {
    StudyType.META_ANALYSIS: 0.15,
    StudyType.SYSTEMATIC_REVIEW: 0.15,
    StudyType.RCT: 0.08,
    StudyType.OBSERVATIONAL: 0.0,
    StudyType.CASE_REPORT: -0.05,
    StudyType.ANIMAL: -0.10,
    StudyType.IN_VITRO: -0.10,
    # Unknown is penalised, not treated as neutral: an unclassifiable source
    # cannot raise the verdict ceiling, so promoting it wastes a prompt slot
    # that a gradeable source could occupy.
    StudyType.UNKNOWN: -0.08,
}

RECENCY_BONUS_MAX = 0.03
RECENCY_FULL_CREDIT_YEARS = 5
RECENCY_ZERO_CREDIT_YEARS = 15

DEFAULT_TOP_K = 8
MIN_ABSTRACT_CHARS = 200

#: pgvector HNSW search breadth. Higher = better recall, slower query. Set
#: per-session because the index-time default is too low for a top-k of 8 over
#: a filtered subset.
HNSW_EF_SEARCH = 64


@dataclass(frozen=True, slots=True)
class RerankConfig:
    top_k: int = DEFAULT_TOP_K
    min_year: int | None = None
    #: Drop anything below this grade before ranking. Left at UNKNOWN (i.e. no
    #: floor) by default: filtering hard here can empty the pool for a fringe
    #: topic, and "no evidence" is a verdict we want to be able to reach
    #: honestly rather than a state we engineer around.
    min_grade: StudyType = StudyType.UNKNOWN
    min_abstract_chars: int = MIN_ABSTRACT_CHARS


def recency_bonus(year: int | None, *, current_year: int | None = None) -> float:
    """Full credit within 5 years, tapering linearly to zero at 15.

    Unknown year scores zero rather than being penalised — missing metadata is
    common for older but still valid work, and study type already carries the
    quality signal.
    """
    if year is None:
        return 0.0
    now = current_year or datetime.now(UTC).year
    age = now - year
    if age <= RECENCY_FULL_CREDIT_YEARS:
        return RECENCY_BONUS_MAX
    if age >= RECENCY_ZERO_CREDIT_YEARS:
        return 0.0
    span = RECENCY_ZERO_CREDIT_YEARS - RECENCY_FULL_CREDIT_YEARS
    return RECENCY_BONUS_MAX * (1 - (age - RECENCY_FULL_CREDIT_YEARS) / span)


def score(cosine: float, study_type: StudyType, year: int | None) -> float:
    """Combine similarity, evidence grade and recency into a final score."""
    return cosine + GRADE_BONUS.get(study_type, 0.0) + recency_bonus(year)


class SemanticReranker:
    def __init__(self, session: AsyncSession, embedder: EmbeddingProvider) -> None:
        self._session = session
        self._embedder = embedder

    async def rank_for_claim(
        self,
        claim: str,
        candidates: list[CachedSource],
        config: RerankConfig | None = None,
    ) -> list[RankedSource]:
        """Rank this run's candidates against one claim; return the top k.

        The query is restricted to **this run's candidate ids**. That
        restriction is the load-bearing detail: this is a re-rank of what the
        scholarly APIs returned for *this* claim, not a similarity search across
        the whole cached library. Dropping it turns the vector store into the
        primary index — the architecture DESIGN.md §4 exists to rule out — and
        starts surfacing papers on unrelated topics that happen to sit nearby in
        embedding space.

        Handles are NOT assigned here. They are assigned once globally across
        the article by ``assign_handles`` below, because two claims sharing a
        source must give it the same handle.
        """
        config = config or RerankConfig()
        if not candidates:
            return []

        claim_vector = await self._embedder.embed_query(claim)
        candidate_ids = [entry.source_id for entry in candidates]

        # Raise search breadth for this transaction only.
        await self._session.execute(text(f"SET LOCAL hnsw.ef_search = {HNSW_EF_SEARCH}"))

        distance = Source.embedding.cosine_distance(claim_vector)
        statement = (
            select(
                Source.id,
                Source.study_type,
                Source.year,
                (1 - distance).cast(Float).label("cosine_similarity"),
            )
            .where(
                Source.id.in_(candidate_ids),
                Source.embedding.isnot(None),
                func.length(Source.abstract) >= config.min_abstract_chars,
            )
            .order_by(distance)
        )
        if config.min_year:
            statement = statement.where(Source.year >= config.min_year)

        result = await self._session.execute(statement)
        rows = result.all()

        by_id = {entry.source_id: entry.paper for entry in candidates}
        floor = EVIDENCE_RANK[config.min_grade]

        scored: list[tuple[float, float, str, CandidatePaper]] = []
        for row in rows:
            study_type = StudyType(row.study_type)
            if EVIDENCE_RANK[study_type] < floor:
                continue
            paper = by_id.get(row.id)
            if paper is None:
                continue
            cosine = float(row.cosine_similarity)
            scored.append((score(cosine, study_type, row.year), cosine, row.id, paper))

        scored.sort(key=lambda entry: entry[0], reverse=True)
        top = scored[: config.top_k]

        logger.info(
            "rerank claim=%r: %d candidates -> %d kept (top score %.3f)",
            claim,
            len(candidates),
            len(top),
            top[0][0] if top else 0.0,
        )

        return [
            RankedSource(
                source_id=source_id,
                claim=claim,
                # Placeholder; replaced by assign_handles.
                citation_handle="S0",
                paper=paper,
                cosine_similarity=cosine,
                final_score=final_score,
            )
            for final_score, cosine, source_id, paper in top
        ]


RankedByClaim = dict[str, list[RankedSource]]


def assign_handles(ranked_by_claim: RankedByClaim) -> RankedByClaim:
    """Assign S1..Sn once, globally, across the whole article.

    Two rules, both learned the hard way:

    * **A source shared by two claims keeps one handle.** Numbering per claim
      would show the model the same paper twice under different names, and it
      cites them as if they were independent findings — manufacturing
      corroboration out of one study.
    * **Numbering follows descending best score.** The model leans on earlier
      handles, so S1 should be the strongest evidence available.
    """
    best_score: dict[str, float] = {}
    for ranked in ranked_by_claim.values():
        for entry in ranked:
            previous = best_score.get(entry.source_id)
            if previous is None or entry.final_score > previous:
                best_score[entry.source_id] = entry.final_score

    ordered = sorted(best_score.items(), key=lambda item: item[1], reverse=True)
    handles = {source_id: f"S{index}" for index, (source_id, _) in enumerate(ordered, start=1)}

    return {
        claim: [
            entry.model_copy(update={"citation_handle": handles[entry.source_id]})
            for entry in ranked
        ]
        for claim, ranked in ranked_by_claim.items()
    }
