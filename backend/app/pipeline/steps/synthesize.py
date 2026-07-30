"""Stage 4 — the RAG generation (LLM call 2)."""

from __future__ import annotations

import logging

from app.domain.contracts import PromptSource, SynthesisInput
from app.llm.base import LLMError, SynthesisClient
from app.pipeline.stages import PipelineContext, StageError, StageName

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
        """
        if ctx.extraction is None:
            raise StageError(self.name, "extraction stage did not run")

        payload = self._build_input(ctx)

        # Zero sources is legitimate — a fringe topic with no literature should
        # produce an honest 'no evidence' article. But the synthesis contract
        # requires at least one source, so short-circuit rather than call the
        # model with an empty grounding set.
        if not payload.sources:
            raise StageError(
                self.name,
                "no sources survived ranking; nothing to ground an article in",
            )

        try:
            result = await self._client.synthesize(payload)
        except LLMError as exc:
            raise StageError(self.name, str(exc)) from exc

        ctx.record_metrics(
            self.name,
            {
                "verdict": str(result.output.verdict),
                "sources_in_prompt": len(payload.sources),
                "handles_cited": len(result.output.all_cited_handles()),
                "body_words": len(result.output.body.as_text().split()),
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "cache_read_tokens": result.usage.cache_read_tokens,
            },
        )

        return ctx.model_copy(
            update={
                "synthesis_input": payload,
                "draft": result.output,
                "usage": ctx.usage + result.usage,
            }
        )

    @staticmethod
    def _build_input(ctx: PipelineContext) -> SynthesisInput:
        """Flatten per-claim ranked sources into one handle-ordered list.

        Deduplicated by handle: a source backing two claims appears once in the
        prompt. Presenting it twice would invite the model to cite it as two
        separate findings.
        """
        assert ctx.extraction is not None

        by_handle: dict[str, PromptSource] = {}
        for ranked in ctx.ranked.values():
            for entry in ranked:
                if entry.citation_handle in by_handle:
                    continue
                by_handle[entry.citation_handle] = PromptSource(
                    handle=entry.citation_handle,
                    title=entry.paper.title,
                    abstract=entry.paper.abstract,
                    journal=entry.paper.journal,
                    year=entry.paper.year,
                    study_type=entry.paper.study_type,
                    source_id=entry.source_id,
                )

        ordered = sorted(by_handle.values(), key=lambda s: int(s.handle[1:]))
        return SynthesisInput(
            product=ctx.extraction.product,
            target_claims=ctx.extraction.target_claims,
            sources=ordered,
        )
