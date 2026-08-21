"""Stage 4 — the RAG generation (LLM call 2)."""

from __future__ import annotations

import logging

from app.domain.contracts import PromptSource, SynthesisInput
from app.llm.base import LLMError, SynthesisClient
from app.pipeline.stages import PipelineContext, StageError, StageName
from app.services.no_evidence import no_evidence_draft

logger = logging.getLogger(__name__)


class SynthesizeStage:
    name = StageName.SYNTHESIZE

    def __init__(self, client: SynthesisClient) -> None:
        self._client = client

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Write the article, grounded strictly in the ranked abstracts.

        The ``SynthesisInput`` built here is carried forward on the context
        unchanged, because VALIDATE needs the *exact* handle set rendered into
        the prompt. Rebuilding it in the validate stage would look equivalent
        and would quietly break invariant #2.

        This stage does not check its own output. Schema conformance is
        guaranteed by structured outputs; grounding is VALIDATE's job. Keeping
        them apart is what stops the generator from also being the approver.

        **Zero sources takes the template branch, not an error.** RETRIEVE
        deliberately lets an empty candidate set through so that a fringe trend
        can be reported honestly; failing here would discard that at the last
        step and turn the most useful thing this product can say into a
        ``draft_failed`` run. See ``services/no_evidence.py``.
        """
        if ctx.extraction is None:
            raise StageError(self.name, "extraction stage did not run")

        payload = self._build_input(ctx)

        if not payload.sources:
            return self._no_evidence(ctx, payload)

        try:
            result = await self._client.synthesize(payload)
        except LLMError as exc:
            # Synthesis carries the pipeline's largest token budget, so a
            # failure here is the most expensive thing that can happen and the
            # one least worth reporting as free. See LLMError.usage.
            ctx.record_usage(self.name, exc.model, exc.usage)
            raise StageError(self.name, str(exc)) from exc

        ctx.record_usage(self.name, result.model, result.usage)

        ctx.record_metrics(
            self.name,
            {
                "verdict": str(result.output.verdict),
                "sources_in_prompt": len(payload.sources),
                "handles_cited": len(result.output.all_cited_handles()),
                "body_words": len(result.output.body.as_text().split()),
            },
        )

        return ctx.model_copy(
            update={"synthesis_input": payload, "draft": result.output}
        )

    def _no_evidence(
        self, ctx: PipelineContext, payload: SynthesisInput
    ) -> PipelineContext:
        """Take the deterministic branch: no model call, no tokens, no cost.

        Records *why* the source list was empty. The two causes are the same
        sentence to a reader and different problems to us: nothing published on
        the topic is the product working as intended, whereas candidates
        retrieved and then filtered away is a signal that ``min_year`` or the
        grade floor is tighter than the corpus can satisfy. Distinguishing them
        after the fact means reading two stages of metrics, so it is recorded
        here where both numbers are in hand.
        """
        retrieved = sum(len(papers) for papers in ctx.candidates.values())
        cause = "no_candidates_retrieved" if retrieved == 0 else "all_candidates_filtered"

        draft = no_evidence_draft(payload.product, payload.target_claims)

        logger.info(
            "no usable sources for %r (%s, %d retrieved); "
            "writing the deterministic no-evidence article",
            payload.product,
            cause,
            retrieved,
        )

        ctx.record_metrics(
            self.name,
            {
                "verdict": str(draft.verdict),
                "sources_in_prompt": 0,
                "handles_cited": 0,
                "body_words": len(draft.body.as_text().split()),
                "model_called": False,
                "no_evidence_cause": cause,
                "candidates_retrieved": retrieved,
            },
        )

        return ctx.model_copy(update={"synthesis_input": payload, "draft": draft})

    @staticmethod
    def _build_input(ctx: PipelineContext) -> SynthesisInput:
        """Flatten per-claim ranked sources into one handle-ordered list.

        Deduplicated by handle: a source backing two claims appears once in the
        prompt. Presenting it twice would invite the model to cite it as two
        separate findings.
        """
        assert ctx.extraction is not None

        by_handle: dict[str, PromptSource] = {}
        for claim, ranked in ctx.ranked.items():
            for entry in ranked:
                if (existing := by_handle.get(entry.citation_handle)) is not None:
                    # Still one entry in the prompt — but it now records every
                    # claim it answers, which is what the per-claim quorum
                    # counts against. Dropping the second claim outright, as
                    # this did, made a shared source invisible to one of them.
                    if claim not in existing.claims:
                        existing.claims.append(claim)
                    continue
                by_handle[entry.citation_handle] = PromptSource(
                    handle=entry.citation_handle,
                    title=entry.paper.title,
                    abstract=entry.paper.abstract,
                    journal=entry.paper.journal,
                    year=entry.paper.year,
                    study_type=entry.paper.study_type,
                    source_id=entry.source_id,
                    claims=[claim],
                )

        ordered = sorted(by_handle.values(), key=lambda s: int(s.handle[1:]))
        return SynthesisInput(
            product=ctx.extraction.product,
            target_claims=ctx.extraction.target_claims,
            sources=ordered,
        )
