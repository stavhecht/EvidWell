"""Stage 4 — give the synthesis model passages from the full text of the
strongest open-access papers.

1. Ask Europe PMC which ranked papers have an open-access full text, by PMID,
   or by DOI for a paper with none.
2. Choose up to ``max_papers`` of them. The claims take turns: each picks its
   highest-ranked open-access paper not yet chosen, so every claim gets one
   before any claim gets a second.
3. Download each chosen paper and split its body with ``chunk_text``.
4. Keep the ``EXCERPTS_PER_PAPER`` chunks closest to the claims the paper was
   retrieved for, in reading order.

Ranking is not touched. Papers are still ranked on their abstracts: only about
half have a full text, and ranking on it would reward being open access rather
than being good evidence. Excerpts only add detail to papers that already made
the cut.

**This stage never fails a run.** Without excerpts the article is written from
abstracts alone, exactly as before this stage existed, and ``cause`` in the
metrics says why there were none.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import defaultdict
from typing import Any

from app.domain.contracts import Excerpt, RankedSource
from app.llm.embeddings.base import EmbeddingProvider
from app.pipeline.stages import PipelineContext, StageName
from app.retrieval.base import ProviderError
from app.retrieval.chunking import chunk_text
from app.retrieval.full_text import FullTextClient

logger = logging.getLogger(__name__)

#: Two ~300-word passages per paper: room for a dose, a duration and an effect
#: size. At six papers that is about 5,000 prompt tokens in all.
EXCERPTS_PER_PAPER = 2


class FullTextStage:
    name = StageName.FULL_TEXT

    def __init__(
        self, client: FullTextClient, embedder: EmbeddingProvider, max_papers: int
    ) -> None:
        self._client = client
        self._embedder = embedder
        self._max_papers = max_papers

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        metrics: dict[str, Any] = {"cause": None}
        excerpts: dict[str, list[Excerpt]] = {}
        try:
            excerpts = await self._excerpts(ctx.ranked, metrics)
        except Exception as exc:
            # Broad on purpose, as in IllustrateStage: a bug here must not fail
            # every run for an enhancement. The traceback reaches the log and
            # the type reaches the metrics.
            logger.exception("full text raised unexpectedly for run %s", ctx.run_id)
            metrics.update(cause="unexpected", detail=f"{type(exc).__name__}: {exc}")

        metrics["excerpts"] = sum(len(found) for found in excerpts.values())
        ctx.record_metrics(self.name, metrics)
        return ctx.model_copy(update={"excerpts": excerpts})

    async def _excerpts(
        self, ranked: dict[str, list[RankedSource]], metrics: dict[str, Any]
    ) -> dict[str, list[Excerpt]]:
        if self._max_papers <= 0:
            metrics["cause"] = "disabled"
            return {}
        # A paper is looked up by its PMID, or by its DOI when it has none —
        # RETRIEVE has already filled in every PMID Europe PMC knows, but a
        # paper in PMC with no PubMed record still has a PMCID under its DOI.
        keys: dict[str, str] = {}
        for entries in ranked.values():
            for entry in entries:
                if entry.paper.pmid:
                    keys[entry.source_id] = f"pmid:{entry.paper.pmid.strip()}"
                elif entry.paper.doi:
                    keys[entry.source_id] = f"doi:{entry.paper.doi.strip().lower()}"
        if not keys:
            metrics["cause"] = "no_sources"
            return {}

        # Step 1.
        pmids = sorted({key[5:] for key in keys.values() if key.startswith("pmid:")})
        dois = sorted({key[4:] for key in keys.values() if key.startswith("doi:")})
        try:
            pmcids = await self._client.open_access_pmcids(pmids, dois)
        except ProviderError as exc:
            logger.warning("full text: open-access lookup failed: %s", exc)
            metrics.update(cause="lookup_failed", detail=str(exc))
            return {}
        open_access = {
            source_id: pmcids[key] for source_id, key in keys.items() if key in pmcids
        }

        # Step 2.
        chosen = choose_papers(ranked, open_access, self._max_papers)
        metrics.update(
            papers=len(keys),
            looked_up_by_doi=len(dois),
            open_access=len(open_access),
            chosen=len(chosen),
        )
        if not chosen:
            metrics["cause"] = "no_open_access"
            return {}

        # Step 3. One failed download costs that paper its excerpts, nothing more.
        downloads = await asyncio.gather(
            *(self._client.sections(pmcid) for pmcid in chosen.values()),
            return_exceptions=True,
        )
        passages: list[tuple[str, Excerpt]] = []
        failed = skipped = 0
        for (source_id, pmcid), result in zip(chosen.items(), downloads, strict=True):
            if isinstance(result, BaseException):
                failed += 1
                logger.warning("full text: %s could not be read: %s", pmcid, result)
                continue
            for section in result:
                for text in chunk_text(section.text):
                    if describes_literature_search(text):
                        skipped += 1
                        continue
                    passages.append((source_id, Excerpt(section=section.title, text=text)))
        metrics.update(failed=failed, search_passages_skipped=skipped)
        if not passages:
            metrics["cause"] = "download_failed" if failed else "no_body_text"
            return {}

        # Step 4.
        texts = [excerpt.text for _, excerpt in passages]
        vectors = await self._embedder.embed_documents(texts)
        claim_vectors = {claim: await self._embedder.embed_query(claim) for claim in ranked}
        claims_of: dict[str, list[list[float]]] = defaultdict(list)
        for claim, entries in ranked.items():
            for entry in entries:
                claims_of[entry.source_id].append(claim_vectors[claim])
        return best_excerpts(passages, vectors, claims_of, EXCERPTS_PER_PAPER)


#: Phrases that describe how a review searched the literature, not what it
#: found. Specific to searching on purpose: "were screened" and "eligibility
#: criteria" also describe a trial's recruitment, which carries its sample size.
_LITERATURE_SEARCH_RE = re.compile(
    r"literature search|search strateg|search terms|databases?\b|PRISMA"
    r"|records? (?:were )?(?:identified|screened)"
    r"|articles? (?:were )?(?:identified|screened|retrieved)"
    r"|full[- ]text articles|duplicates? (?:were )?removed",
    re.IGNORECASE,
)


def describes_literature_search(text: str) -> bool:
    """A passage about a review's search, like "The literature search identified
    4788 articles from PubMed/MEDLINE, EMBASE…".

    Measured 2026-09-29: such a passage was one of the two excerpts chosen for
    a systematic review, and a variant ranking against "product: claim" chose
    two of them. It mentions the claim's words as search terms, so it scores
    as close to the claim as a finding does (0.71 against 0.712), and it gives
    the model nothing to write about. Two distinct phrases are required, so one
    passing mention of a database does not cost a findings passage.
    """
    return len({match.group(0).lower() for match in _LITERATURE_SEARCH_RE.finditer(text)}) >= 2


def choose_papers(
    ranked: dict[str, list[RankedSource]], open_access: dict[str, str], limit: int
) -> dict[str, str]:
    """Up to ``limit`` open-access papers, as source id -> PMCID.

    The claims take turns, each picking its highest-ranked open-access paper
    not already chosen, so every claim gets one before any claim gets a second.
    """
    queues = [
        [entry.source_id for entry in entries if entry.source_id in open_access]
        for entries in ranked.values()
    ]
    chosen: dict[str, str] = {}
    while len(chosen) < limit and any(queues):
        for queue in queues:
            while queue and queue[0] in chosen:
                queue.pop(0)
            if queue and len(chosen) < limit:
                source_id = queue.pop(0)
                chosen[source_id] = open_access[source_id]
    return chosen


def best_excerpts(
    passages: list[tuple[str, Excerpt]],
    vectors: list[list[float]],
    claim_vectors: dict[str, list[list[float]]],
    per_paper: int,
) -> dict[str, list[Excerpt]]:
    """For each paper, its ``per_paper`` passages closest to any of its claims.

    Chosen by similarity, then put back in reading order, so a Methods passage
    still comes before a Results one.
    """
    scored: dict[str, list[tuple[float, int, Excerpt]]] = defaultdict(list)
    for position, ((source_id, excerpt), vector) in enumerate(
        zip(passages, vectors, strict=True)
    ):
        closeness = max(_cosine(vector, claim) for claim in claim_vectors[source_id])
        scored[source_id].append((closeness, position, excerpt))

    best: dict[str, list[Excerpt]] = {}
    for source_id, entries in scored.items():
        top = sorted(entries, key=lambda entry: entry[0], reverse=True)[:per_paper]
        best[source_id] = [excerpt for _, _, excerpt in sorted(top, key=lambda entry: entry[1])]
    return best


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norms = math.sqrt(sum(a * a for a in left)) * math.sqrt(sum(b * b for b in right))
    return dot / norms if norms else 0.0
