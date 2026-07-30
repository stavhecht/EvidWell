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
    """Recorded per stage in ``pipeline_stage_runs.metrics``."""

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
    """A parsed, schema-valid model output plus what it cost."""

    output: T
    usage: TokenUsage


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
    """Transport, rate-limit, or schema failure from a generative call."""


class RefusalError(LLMError):
    """The model declined the request.

    Distinct from a transport failure because retrying identical input will not
    help. Surfaced to the run as a terminal stage error so it is visible in the
    console rather than retried in a loop.
    """
