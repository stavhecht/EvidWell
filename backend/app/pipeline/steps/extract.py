"""Stage 1 — extract claims and ingredients (LLM call 1)."""

from __future__ import annotations

import logging

from app.domain.contracts import ExtractionInput
from app.llm.base import ExtractionClient, LLMError
from app.pipeline.stages import PipelineContext, StageError, StageName

logger = logging.getLogger(__name__)


class ExtractStage:
    name = StageName.EXTRACT

    def __init__(self, client: ExtractionClient) -> None:
        self._client = client

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Turn topic + optional blurb into structured claims and ingredients."""
        try:
            result = await self._client.extract(
                ExtractionInput(topic=ctx.topic, blurb=ctx.blurb)
            )
        except LLMError as exc:
            raise StageError(self.name, str(exc)) from exc

        # Claim count is the fan-out multiplier for everything downstream (two
        # queries per provider per claim), so it is the first number to look at
        # when a run is unexpectedly slow or expensive.
        ctx.record_metrics(
            self.name,
            {
                "claims": len(result.output.target_claims),
                "ingredients": len(result.output.ingredients),
                "tokens": result.usage.output_tokens,
            },
        )

        return ctx.model_copy(
            update={"extraction": result.output, "usage": ctx.usage + result.usage}
        )
