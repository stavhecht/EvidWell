"""Claude-backed implementations of the two generative calls.

Model choices and constraints, for reviewers unfamiliar with this API surface:

* **The two calls run on different models**, because they are different jobs.
  Extraction is a short, mechanical structured response — a small model is
  sufficient. Synthesis is where the product's guarantees live: it must cite
  only supplied handles (invariant #2) and keep its verdict under the evidence
  ceiling (invariant #3), so it gets the stronger model. Both are config, not
  constants — see ``settings.extraction_model`` / ``settings.synthesis_model``.
* Structured outputs via ``messages.parse(output_format=...)``. The response is
  constrained to the Pydantic schema and comes back as a validated instance,
  which eliminates malformed-JSON handling entirely. Supported on every model
  named below; verify before configuring an older one.
* ``temperature`` / ``top_p`` / ``top_k`` are **rejected** on Opus 5 and
  Sonnet 5 — a request carrying them returns a 400. Behaviour is steered by
  prompt only. (Older/smaller models still accept them; this code sends none
  either way, so it is safe across the range.)
* ``max_tokens`` caps thinking **plus** visible output on models where thinking
  is on by default (Opus 5, Sonnet 5). Synthesis is therefore given 8K even
  though the article is ~300 words; sizing ``max_tokens`` to the article would
  truncate mid-response. Both calls always pass an explicit cap — never rely on
  a default.
* The system prompt is byte-stable across every call and carries the
  ``cache_control`` breakpoint. Anything per-article goes in the user turn,
  after it — a single volatile byte in the prefix would invalidate the cache
  for everything following it.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic
from anthropic import AsyncAnthropic

from app.domain.contracts import (
    ExtractionInput,
    ExtractionOutput,
    SynthesisInput,
    SynthesisOutput,
)
from app.llm.base import LLMError, LLMResult, RefusalError, TokenUsage
from app.llm.prompts.extraction import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_user_prompt,
)
from app.llm.prompts.synthesis import (
    SYNTHESIS_SYSTEM_PROMPT,
    build_synthesis_user_prompt,
)

logger = logging.getLogger(__name__)

# Defaults only — the orchestrator passes settings.extraction_model /
# settings.synthesis_model. Split by job: extraction is mechanical, synthesis
# carries the grounding and verdict-ceiling guarantees.
EXTRACTION_MODEL = "claude-haiku-4-5"
SYNTHESIS_MODEL = "claude-sonnet-5"

# Every call passes an explicit cap; there is no unbounded request in this file.
# Extraction is a short structured response; synthesis needs headroom for
# thinking on top of a ~300-word article.
EXTRACTION_MAX_TOKENS = 2_000
SYNTHESIS_MAX_TOKENS = 8_000


def _usage_from_response(response: Any) -> TokenUsage:
    """Map the SDK usage object onto our own record."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    return TokenUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
        cache_write_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
    )


def _system_blocks(prompt: str) -> list[dict[str, Any]]:
    """The stable system prefix, marked as the cache breakpoint."""
    return [
        {
            "type": "text",
            "text": prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _check_refusal(response: Any, call: str) -> None:
    """Raise before any attempt to read parsed output.

    On a refusal there is no parsed output to read, so this has to run first —
    reading ``parsed_output`` on a refused response is an AttributeError that
    obscures what actually happened.
    """
    if getattr(response, "stop_reason", None) == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise RefusalError(
            f"{call}: model declined the request"
            + (f" (category={category})" if category else "")
        )


class AnthropicExtractionClient:
    """LLM call 1 — claim and ingredient extraction."""

    def __init__(self, client: AsyncAnthropic, model: str = EXTRACTION_MODEL) -> None:
        self._client = client
        self._model = model

    async def extract(self, payload: ExtractionInput) -> LLMResult[ExtractionOutput]:
        """Extract product, claims and ingredients from a topic + optional blurb.

        Raises:
            RefusalError: the model declined; retrying identical input won't help.
            LLMError: transport, rate limit, or schema failure.
        """
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=EXTRACTION_MAX_TOKENS,
                system=_system_blocks(EXTRACTION_SYSTEM_PROMPT),
                messages=[
                    {"role": "user", "content": build_extraction_user_prompt(payload)}
                ],
                output_format=ExtractionOutput,
            )
        except anthropic.APIError as exc:
            raise LLMError(f"extraction call failed: {exc}") from exc

        _check_refusal(response, "extraction")

        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("extraction returned no parsed output")

        logger.info(
            "extracted %d claims for topic=%r", len(parsed.target_claims), payload.topic
        )
        return LLMResult(output=parsed, usage=_usage_from_response(response))


class AnthropicSynthesisClient:
    """LLM call 2 — the RAG generation."""

    def __init__(self, client: AsyncAnthropic, model: str = SYNTHESIS_MODEL) -> None:
        self._client = client
        self._model = model

    async def synthesize(self, payload: SynthesisInput) -> LLMResult[SynthesisOutput]:
        """Write a grounded article from the retrieved abstracts.

        Returns whatever the model produced: schema-valid, but **unverified**.
        This method deliberately does not check grounding, citation resolution,
        or the verdict cap — that is ``validate_draft()``'s job, and keeping the
        two apart is what stops "the thing that generates" from also being "the
        thing that approves".

        Raises:
            RefusalError: the model declined.
            LLMError: transport, rate limit, or schema failure.
        """
        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=SYNTHESIS_MAX_TOKENS,
                system=_system_blocks(SYNTHESIS_SYSTEM_PROMPT),
                messages=[
                    {"role": "user", "content": build_synthesis_user_prompt(payload)}
                ],
                output_format=SynthesisOutput,
            )
        except anthropic.APIError as exc:
            raise LLMError(f"synthesis call failed: {exc}") from exc

        _check_refusal(response, "synthesis")

        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("synthesis returned no parsed output")

        logger.info(
            "synthesised article verdict=%s citing %d/%d sources",
            parsed.verdict,
            len(parsed.all_cited_handles()),
            len(payload.sources),
        )
        return LLMResult(output=parsed, usage=_usage_from_response(response))


def build_anthropic_client(api_key: str) -> AsyncAnthropic:
    """Shared transport for both call sites.

    ``max_retries`` covers 429s and 5xx with backoff. The timeout is generous
    because synthesis runs with thinking on, and a thinking-heavy turn on a
    long source block is genuinely slow.
    """
    return AsyncAnthropic(api_key=api_key, max_retries=3, timeout=180.0)
