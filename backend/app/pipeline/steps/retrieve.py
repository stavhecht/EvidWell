"""Stage 2 — keyword recall against the scholarly APIs.

Pass 1 of the hybrid retrieval described in DESIGN.md §4. Fan out across
providers per claim, normalise, dedup, cache.
"""

from __future__ import annotations

import asyncio
import logging

from app.domain.contracts import CachedCandidate, CandidatePaper
from app.evidence.grading import is_protocol, is_retracted
from app.pipeline.stages import PipelineContext, StageError, StageName
from app.retrieval.base import RateLimited, ScholarlyProvider
from app.retrieval.cache import SourceCache
from app.retrieval.dedup import merge_candidates, unique_papers
from app.retrieval.query_builder import QueryStrategy, UnanchoredQuery

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

        **Partial failure within a claim is tolerated.** One API being down
        while another answers degrades recall, and degraded recall produces a
        more cautious verdict — an acceptable outcome.

        **A claim where every call failed is not.** Zero candidates is a valid
        result: a fringe trend with no literature must reach synthesis so the
        article can honestly say "no evidence", and raising there would turn
        the most important thing this product can say into a crash. But that
        holds only when the search actually ran. A rate-limited claim yields
        the identical empty list, and ``SynthesizeStage`` now turns an empty
        list into a finished, confident-sounding article rather than crashing
        (DESIGN.md §5). "We could not search" and "we searched and found
        nothing" are indistinguishable from here on, so they are separated
        here, while the difference is still visible.

        **A claim whose query names no substance is not either of those.** It
        is a search that ran cleanly and asked the wrong question, and it is
        the most dangerous of the three because every downstream signal reads
        as success: papers come back, ranking scores them, synthesis cites
        them, validation resolves every handle. Only this stage can still see
        that the query never mentioned the product, so ``UnanchoredQuery``
        fails here rather than degrading into a keyword search.
        """
        if ctx.extraction is None:
            raise StageError(self.name, "extraction stage did not run")

        raw_by_claim: dict[str, list[CandidatePaper]] = {}
        provider_hits: dict[str, int] = {}
        unsearched: list[str] = []
        failures = 0
        rate_limited = 0
        protocols_dropped = 0
        retractions_dropped = 0

        for claim in ctx.extraction.target_claims:
            try:
                queries = self._strategy.build(
                    claim, ctx.extraction.product, ctx.extraction.ingredients
                )
            except UnanchoredQuery as exc:
                # Not retryable. The subject terms come from the extraction, not
                # from anything that varies between attempts, so all three
                # attempts would compose the identical unusable query — and
                # unlike the throttle case below there is no external condition
                # that clears. The fix is upstream, in what extraction returned.
                raise StageError(self.name, str(exc), retryable=False) from exc

            tasks = [
                provider.search(query)
                for query in queries
                for provider in self._providers
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            collected: list[CandidatePaper] = []
            searched = 0
            for index, result in enumerate(results):
                provider = self._providers[index % len(self._providers)]
                if isinstance(result, BaseException):
                    failures += 1
                    if isinstance(result, RateLimited):
                        rate_limited += 1
                    logger.warning(
                        "provider %s failed for claim %r: %s",
                        provider.source_api,
                        claim,
                        result,
                    )
                    continue
                searched += 1
                # Protocols and retracted papers are dropped here, before dedup
                # and before the cache, so neither ever occupies a candidate
                # slot or reaches a prompt.
                #
                # A protocol is a trial's *plan* — it reports no findings at
                # all, so there is no verdict it could honestly support. A
                # retracted paper is the stronger case: it does report a
                # finding, and the finding is one the literature has withdrawn.
                # Both were already capped at `unknown` by the classifier, and
                # a cap was never the answer — it limits how confident the
                # article may sound while leaving the citation in place. See
                # evidence/grading.py::is_protocol and ::is_retracted.
                #
                # This only catches what was already retracted when we first
                # saw it. Retractions issued *after* caching are the sweep's
                # job — scripts/check_retractions.py.
                kept = []
                for paper in result:
                    if is_protocol(paper.raw_study_type, paper.title):
                        protocols_dropped += 1
                    elif is_retracted(paper.raw_study_type):
                        retractions_dropped += 1
                        logger.info(
                            "refused retracted paper pmid=%s doi=%s: %r",
                            paper.pmid,
                            paper.doi,
                            paper.title,
                        )
                    else:
                        kept.append(paper)
                collected.extend(kept)
                provider_hits[provider.source_api] = provider_hits.get(
                    provider.source_api, 0
                ) + len(kept)

            # Counted per claim, not per run: a run where two claims searched
            # cleanly and a third was throttled still publishes a verdict on
            # evidence it never looked for.
            if tasks and not searched:
                unsearched.append(claim)
            raw_by_claim[claim] = collected

        if unsearched:
            # Retryable — the usual cause is a throttle that has since cleared,
            # and the alternative to retrying is publishing a guess.
            detail = f"; {rate_limited} rate limited" if rate_limited else ""
            raise StageError(
                self.name,
                f"{len(unsearched)} of {len(raw_by_claim)} claims went unsearched "
                f"(every provider call failed{detail}): "
                f"{', '.join(repr(claim) for claim in unsearched)}",
                retryable=True,
            )

        # Deduplicate across every claim at once, not per claim. A paper
        # answering two claims has to come out as the same object in both
        # lists — RankStage and the cache both key on ``dedup_key``, and a
        # record merged under one claim but not another carries two different
        # keys through the rest of the pipeline. See retrieval/dedup.py.
        candidates = {
            claim: papers[: self._max_candidates]
            for claim, papers in merge_candidates(raw_by_claim).items()
        }
        for claim, papers in candidates.items():
            logger.info(
                "claim %r: %d raw -> %d deduped candidates",
                claim,
                len(raw_by_claim[claim]),
                len(papers),
            )

        # Upsert first, embed second — see cache.py. The reverse re-embeds the
        # whole candidate set on every run.
        unique = unique_papers(candidates)
        cached = await self._cache.upsert_many(unique)
        await self._cache.ensure_embeddings(cached)

        # Pair every candidate with its row id before leaving the stage. This
        # is the only stage that learns those ids, and RankStage needs them;
        # dropping them here is what used to make RankStage re-upsert the whole
        # set to look them up again.
        source_ids = {entry.paper.dedup_key: entry.source_id for entry in cached}
        try:
            paired = {
                claim: [
                    CachedCandidate(source_id=source_ids[paper.dedup_key], paper=paper)
                    for paper in papers
                ]
                for claim, papers in candidates.items()
            }
        except KeyError as exc:
            # Unreachable: upsert_many returns one entry per unique paper and
            # `unique` is exactly those. Raised rather than skipped anyway,
            # because the alternative is dropping a source between retrieval
            # and ranking, which is invisible downstream — it reads as thinner
            # evidence, and thinner evidence reads as a more careful verdict.
            raise StageError(
                self.name, f"source cache returned no row for candidate {exc}"
            ) from exc

        raw_total = sum(len(papers) for papers in raw_by_claim.values())
        ctx.record_metrics(
            self.name,
            {
                "provider_hits": provider_hits,
                "candidates_total": raw_total,
                # Trial protocols refused before dedup. A number that
                # climbs means the queries are drifting toward planned
                # work rather than reported results.
                "protocols_dropped": protocols_dropped,
                # Expected to be 0 almost always — 0 of 92 on the live corpus.
                # A non-zero value is the ingest screen doing its job; a
                # persistently non-zero one means a query is pulling from a
                # corner of the literature worth looking at.
                "retractions_dropped": retractions_dropped,
                "candidates_unique": len(unique),
                # How much cross-provider overlap the fan-out actually found.
                # A number near zero once several providers are enabled means
                # identity matching has stopped working, which is invisible in
                # the article itself — it looks like more corroboration.
                "duplicates_merged": raw_total - sum(len(p) for p in candidates.values()),
                "provider_failures": failures,
                # Split out from provider_failures because the remedy differs:
                # a throttled run wants slower pacing, a failing one wants a
                # look at the provider.
                "rate_limited": rate_limited,
                "cache_hits": sum(1 for entry in cached if entry.had_embedding),
            },
        )

        return ctx.model_copy(update={"candidates": paired})
