"""Provider-agnostic interfaces for the generative calls.

The pipeline depends on these Protocols, never on a concrete client, so a
provider swap or a fake in tests is a constructor argument rather than a
refactor.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.contracts import (
    ExtractionInput,
    ExtractionOutput,
    SynthesisInput,
    SynthesisOutput,
)


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """What one model call consumed, by token class.

    Persisted per stage in ``pipeline_stage_runs`` as four typed columns, and
    rolled up onto ``pipeline_runs``. Deliberately carries no model id and no
    cost: ``__add__`` exists so a run can report a total, and a total is only
    meaningful over tokens. Extraction and synthesis can run on *different*
    models, so summed tokens have no single price — which is why the model id
    lives on ``LLMResult`` and pricing happens per call in ``llm/pricing.py``.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.cache_read_tokens + other.cache_read_tokens,
            self.cache_write_tokens + other.cache_write_tokens,
        )


@dataclass(frozen=True, slots=True)
class LLMResult[T]:
    """A parsed, schema-valid model output plus what it cost.

    ``model`` is the provider-namespaced id (``anthropic/claude-sonnet-5``),
    not the bare setting value. It is the join key into the price table, and it
    has to come back from the call rather than be read from settings at the
    persistence site: a run in flight when someone edits ``SYNTHESIS_MODEL``
    would otherwise be priced against a model it never used.
    """

    output: T
    usage: TokenUsage
    model: str = ""


class ExtractionClient(Protocol):
    """LLM call 1."""

    async def extract(self, payload: ExtractionInput) -> LLMResult[ExtractionOutput]:
        """Extract product, target claims and ingredients from a topic."""
        ...


class SynthesisClient(Protocol):
    """LLM call 2 — the RAG generation.

    Implementations MUST NOT attempt to validate grounding. Producing the draft
    and checking the draft are separate responsibilities on purpose:
    ``evidence/validation.py`` owns the check, and it runs against the prompt's
    handle set and the database — neither of which the model can influence.
    """

    async def synthesize(self, payload: SynthesisInput) -> LLMResult[SynthesisOutput]:
        """Write a grounded article from the retrieved abstracts."""
        ...


class LLMError(RuntimeError):
    """Transport, rate-limit, or schema failure from a generative call.

    Carries ``usage`` when the failure happened *after* the provider answered,
    because those tokens are billed exactly like a successful call's. The
    expensive case is truncation: hitting ``max_tokens`` spends the entire
    output budget — 8K on synthesis, the largest single charge the pipeline can
    incur — and produces nothing. A failed run reporting zero cost would make
    the pipeline look cheapest at the moment it is burning the most, so the
    stage records this before converting the error into a ``StageError``.

    Left empty on a transport failure, where nothing was consumed.
    """

    def __init__(
        self, message: str, *, usage: TokenUsage | None = None, model: str = ""
    ) -> None:
        super().__init__(message)
        self.usage = usage or TokenUsage()
        self.model = model


class RefusalError(LLMError):
    """The model declined the request.

    Distinct from a transport failure because retrying identical input will not
    help. Surfaced to the run as a terminal stage error so it is visible in the
    console rather than retried in a loop.
    """
