"""Stage contract and the context that flows between stages.

Every stage is ``async (PipelineContext) -> PipelineContext``. Two properties
follow from that shape, and both are deliberate:

* **Serialisable context, no shared memory.** Stages communicate only through
  ``PipelineContext``, which is a Pydantic model. That is what makes the AWS
  migration a transport swap rather than a redesign — each stage becomes a
  Lambda, the context becomes the Step Functions state document, and the
  orchestrator calls Invoke instead of the function. Reach for module-level
  state or a shared client cache inside a stage and that property is gone.

* **Room for the agentic loop without changing any stage.** Query refinement,
  when it arrives, is a loop the orchestrator runs around RETRIEVE and RANK:
  re-run with widened queries while the candidate pool is too thin. No stage
  needs to know it is being re-run. Deferred per the brief; the seam is here so
  it stays cheap.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from app.domain.contracts import (
    CandidatePaper,
    ExtractionOutput,
    RankedSource,
    SynthesisInput,
    SynthesisOutput,
    ValidationReport,
)
from app.llm.base import TokenUsage


class StageName(StrEnum):
    """Ordered. ``ordinal`` in pipeline_stage_runs is this enum's position.

    One-to-one with the states of the future Step Functions machine.
    """

    EXTRACT = "extract"
    RETRIEVE = "retrieve"
    RANK = "rank"
    SYNTHESIZE = "synthesize"
    VALIDATE = "validate"
    PERSIST = "persist"


STAGE_ORDER: list[StageName] = list(StageName)


class PipelineContext(BaseModel):
    """State threaded through the pipeline. Accumulates; never mutated in place.

    Each stage returns a copy with its own outputs filled in
    (``ctx.model_copy(update=...)``), so a failed run's context shows exactly
    how far it got.
    """

    run_id: str
    topic: str
    blurb: str | None = None

    # EXTRACT
    extraction: ExtractionOutput | None = None

    # RETRIEVE — candidates per claim, post-dedup
    candidates: dict[str, list[CandidatePaper]] = Field(default_factory=dict)

    # RANK — top-k per claim, with handles assigned
    ranked: dict[str, list[RankedSource]] = Field(default_factory=dict)

    # SYNTHESIZE
    #: The exact payload rendered into the synthesis prompt. Carried forward
    #: rather than rebuilt, because VALIDATE must check against the handle set
    #: the model actually saw. Rebuilding it from `ranked` would look
    #: equivalent and would quietly break invariant #2: any drift between the
    #: two reconstructions creates a gap where a handle validates against a set
    #: that was never in the prompt.
    synthesis_input: SynthesisInput | None = None
    draft: SynthesisOutput | None = None

    # VALIDATE
    validation: ValidationReport | None = None

    # PERSIST
    article_id: str | None = None

    usage: TokenUsage = Field(default_factory=TokenUsage)

    #: Per-stage metrics, written into pipeline_stage_runs.metrics by the
    #: orchestrator. Mutated in place: it is bookkeeping about the run, not
    #: part of the data flowing through it, and threading it through every
    #: model_copy would add noise to each stage for no benefit.
    metrics: dict[str, dict[str, Any]] = Field(default_factory=dict)

    model_config = {"arbitrary_types_allowed": True}

    def record_metrics(self, stage: StageName, values: dict[str, Any]) -> None:
        """Attach observability data for one stage."""
        self.metrics[str(stage)] = values


class Stage(Protocol):
    """One pipeline stage."""

    name: StageName

    async def run(self, ctx: PipelineContext) -> PipelineContext:
        """Transform the context.

        Raise ``StageError`` for a genuine failure. Do not raise to signal an
        empty-but-valid result: zero candidates for a fringe trend is a real
        outcome that must reach synthesis, because ``no_evidence`` is a verdict
        we want to be able to publish honestly, not an error state.
        """
        ...


class StageError(RuntimeError):
    """A stage failed. Recorded on pipeline_stage_runs.error."""

    def __init__(self, stage: StageName, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"[{stage}] {message}")
        self.stage = stage
        self.retryable = retryable
