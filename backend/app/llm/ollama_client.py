"""Ollama-backed implementations of the two generative calls.

Local, free, offline. Same two Protocols as ``llm/anthropic_client.py``, served
by a model running on this machine instead of a paid API, so nothing downstream
of ``llm/base.py`` can tell the difference. Selected with ``LLM_PROVIDER=ollama``
(the current default); ``anthropic`` switches back and touches no other file —
see ``llm/factory.py``.

Differences from the Anthropic client worth knowing before debugging anything
here:

* **Structured output is a decoding grammar, not a validating schema.** The
  ``format`` argument takes a JSON Schema and constrains generation to it, so
  the response is syntactically valid JSON of the right shape. It does *not*
  enforce the Pydantic validators — ``ArticleBody``'s three-sentence ceiling,
  the 12-word headline, the one-clause qualifier — because those live in
  Python, not in the schema. There is no ``messages.parse()`` equivalent here:
  the SDK hands back a string and validation is ours to do. A small local model
  breaks those validators routinely, so this client retries once with the
  errors fed back. The hosted path needs no such thing and does not do it.
* **The schema is flattened before it is sent.** ``model_json_schema()`` emits
  ``$ref``/``$defs``; not every Ollama build resolves them, and a half-resolved
  grammar surfaces as an opaque decode failure. ``_flat_schema`` inlines them.
* **``num_ctx`` is the trap.** Ollama's default context window is 4K and
  overflow is *silent* — the front of the prompt is dropped. For synthesis that
  means sources disappear while the instruction to cite them survives, and the
  model cites handles it can no longer see. Both calls set it explicitly, sized
  for the prompt they send.
* **There is no refusal stop reason.** A local refusal arrives as prose that
  cannot satisfy the grammar, so it surfaces as ``LLMError``. ``RefusalError``
  is raised only by the hosted path.
* **Quality is not comparable.** An 8B local model is here so the pipeline can
  be run end to end for free during development. Expect thinner claim
  extraction and more validation failures than the numbers recorded against
  ``settings.extraction_model``; that is the trade being made, not a bug.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import httpx
from ollama import AsyncClient, ResponseError
from pydantic import BaseModel, ValidationError

from app.domain.contracts import (
    ExtractionInput,
    ExtractionOutput,
    SynthesisInput,
    SynthesisOutput,
)
from app.llm.base import LLMError, LLMResult, TokenUsage
from app.llm.prompts.extraction import (
    EXTRACTION_SYSTEM_PROMPT,
    build_extraction_user_prompt,
)
from app.llm.prompts.synthesis import (
    SYNTHESIS_SYSTEM_PROMPT,
    build_synthesis_user_prompt,
)

logger = logging.getLogger(__name__)

# Defaults only — the orchestrator passes settings.ollama_extraction_model /
# settings.ollama_synthesis_model. llama3.1:8b is the default because it is the
# most commonly already-pulled model that follows a JSON-schema grammar without
# drifting; point synthesis at something larger (qwen2.5:14b-instruct,
# llama3.3:70b) if drafts keep failing citation validation.
EXTRACTION_MODEL = "llama3.1:8b"
SYNTHESIS_MODEL = "llama3.1:8b"

# Context windows, set explicitly because the server default (4K) silently
# truncates. Synthesis carries ~8 abstracts plus the system prompt.
EXTRACTION_NUM_CTX = 8_192
SYNTHESIS_NUM_CTX = 16_384

# Output caps. Unlike the hosted models these count visible output only —
# there is no thinking budget folded in — so they are sized to the response.
EXTRACTION_MAX_TOKENS = 2_000
SYNTHESIS_MAX_TOKENS = 4_000

# Local models accept sampling parameters (the Opus 5 restriction noted in
# anthropic_client.py does not apply here). Extraction is mechanical, so it is
# greedy; synthesis writes prose, where greedy decoding on a small model
# degrades into repetition.
EXTRACTION_TEMPERATURE = 0.0
SYNTHESIS_TEMPERATURE = 0.4

#: One repair round trip. The grammar guarantees shape, so a failure here is a
#: Pydantic *validator* failure — "beat 2 is four sentences" — which a model can
#: usually fix when told. Two attempts, not more: past that it is not going to
#: converge and the run should fail visibly rather than burn minutes.
MAX_ATTEMPTS = 2


def _inline_refs(node: Any, defs: dict[str, Any]) -> Any:
    """Replace every ``$ref`` with the definition it points at.

    Assumes the contract models are acyclic — they are, and a cycle would
    recurse until Python complains, which is a louder failure than the
    silently-degraded grammar this exists to prevent.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs[ref.removeprefix("#/$defs/")]
            # Keys alongside the $ref (a description, say) win over the target's.
            siblings = {k: v for k, v in node.items() if k != "$ref"}
            return {**_inline_refs(target, defs), **siblings}
        return {k: _inline_refs(v, defs) for k, v in node.items() if k != "$defs"}
    if isinstance(node, list):
        return [_inline_refs(item, defs) for item in node]
    return node


@lru_cache(maxsize=8)
def _flat_schema(model: type[BaseModel]) -> dict[str, Any]:
    """A self-contained JSON Schema for ``model``, cached per class."""
    schema = model.model_json_schema()
    flat = _inline_refs(schema, schema.get("$defs", {}))
    assert isinstance(flat, dict)
    return flat


def _usage_from_response(response: Any) -> TokenUsage:
    """Map the SDK's counters onto our own record.

    Ollama reuses a cached prompt prefix internally but does not report it, so
    the cache fields stay zero rather than being guessed at.
    """
    return TokenUsage(
        input_tokens=getattr(response, "prompt_eval_count", 0) or 0,
        output_tokens=getattr(response, "eval_count", 0) or 0,
    )


def _validation_feedback(exc: ValidationError) -> str:
    """The failed constraints, phrased for the model rather than for a log."""
    lines = [
        f"- {'.'.join(str(part) for part in err['loc']) or 'response'}: {err['msg']}"
        for err in exc.errors()
    ]
    return "\n".join(lines[:10])


async def _chat(
    client: AsyncClient,
    *,
    call: str,
    model: str,
    system: str,
    user: str,
    output_format: type[BaseModel],
    num_ctx: int,
    max_tokens: int,
    temperature: float,
) -> LLMResult[Any]:
    """One structured-output turn, with a single validation-repair retry.

    Usage is accumulated across attempts: the tokens were genuinely spent, and a
    stage that quietly under-reports its second attempt makes the run record lie
    about what happened.

    Raises:
        LLMError: transport failure, missing model, or output that still fails
            validation after ``MAX_ATTEMPTS``.
    """
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    usage = TokenUsage()
    last_error: ValidationError | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await client.chat(
                model=model,
                messages=messages,
                format=_flat_schema(output_format),
                stream=False,
                options={
                    "temperature": temperature,
                    "num_ctx": num_ctx,
                    "num_predict": max_tokens,
                },
            )
        except ResponseError as exc:
            if exc.status_code == 404:
                raise LLMError(
                    f"{call}: ollama does not have model {model!r} — run "
                    f"`ollama pull {model}`"
                ) from exc
            raise LLMError(f"{call} call failed: {exc}") from exc
        except (ConnectionError, httpx.HTTPError) as exc:
            # The SDK turns a refused connection into a builtin ConnectionError
            # and lets httpx timeouts through untouched; neither is a
            # ResponseError, so both are caught here rather than escaping the
            # stage as an unrecognised exception type.
            raise LLMError(
                f"{call} call failed: {exc} — is `ollama serve` running?"
            ) from exc

        usage = usage + _usage_from_response(response)
        content = response.message.content or ""

        try:
            # Shape is already guaranteed by the grammar; this is where the
            # contract's own validators actually get applied.
            parsed = output_format.model_validate_json(content)
        except ValidationError as exc:
            last_error = exc
            logger.warning(
                "%s: attempt %d/%d failed contract validation: %s",
                call,
                attempt,
                MAX_ATTEMPTS,
                _validation_feedback(exc).replace("\n", "; "),
            )
            messages = [
                *messages[:2],
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": (
                        "That response broke these constraints:\n"
                        f"{_validation_feedback(exc)}\n\n"
                        "Return the same content corrected to satisfy every one "
                        "of them. Shorten rather than rewrite, and keep every "
                        "citation handle exactly as it was."
                    ),
                },
            ]
            continue

        return LLMResult(output=parsed, usage=usage)

    raise LLMError(
        f"{call}: output still failed validation after {MAX_ATTEMPTS} attempts "
        f"({last_error})"
    )


class OllamaExtractionClient:
    """LLM call 1 — claim and ingredient extraction, run locally."""

    def __init__(self, client: AsyncClient, model: str = EXTRACTION_MODEL) -> None:
        self._client = client
        self._model = model

    async def extract(self, payload: ExtractionInput) -> LLMResult[ExtractionOutput]:
        """Extract product, claims and ingredients from a topic + optional blurb.

        Raises:
            LLMError: transport, missing model, or output that fails the
                contract twice. A local model has no refusal stop reason, so
                ``RefusalError`` is never raised from here.
        """
        result = await _chat(
            self._client,
            call="extraction",
            model=self._model,
            system=EXTRACTION_SYSTEM_PROMPT,
            user=build_extraction_user_prompt(payload),
            output_format=ExtractionOutput,
            num_ctx=EXTRACTION_NUM_CTX,
            max_tokens=EXTRACTION_MAX_TOKENS,
            temperature=EXTRACTION_TEMPERATURE,
        )
        parsed: ExtractionOutput = result.output

        if not parsed.ingredients:
            # Not fatal, but it unanchors the literature search — the query
            # builder falls back to claim keywords, turning "ashwagandha for
            # stress" into a search for stress in general. Small local models do
            # this far more often than the hosted ones (see the measurement in
            # config.py against extraction_model).
            logger.warning(
                "extraction returned no ingredients for topic=%r; retrieval will "
                "fall back to claim keywords",
                payload.topic,
            )

        logger.info(
            "extracted %d claims for topic=%r", len(parsed.target_claims), payload.topic
        )
        return LLMResult(output=parsed, usage=result.usage)


class OllamaSynthesisClient:
    """LLM call 2 — the RAG generation, run locally.

    Like the hosted client, this produces a draft and nothing more. Grounding,
    citation resolution and the verdict ceiling are checked afterwards by
    ``evidence/validation.py`` against the prompt's handle set and the database.
    Running the model locally changes who generates, not who approves.
    """

    def __init__(self, client: AsyncClient, model: str = SYNTHESIS_MODEL) -> None:
        self._client = client
        self._model = model

    async def synthesize(self, payload: SynthesisInput) -> LLMResult[SynthesisOutput]:
        """Write a grounded article from the retrieved abstracts.

        Raises:
            LLMError: transport, missing model, or output that fails the
                contract twice.
        """
        result = await _chat(
            self._client,
            call="synthesis",
            model=self._model,
            system=SYNTHESIS_SYSTEM_PROMPT,
            user=build_synthesis_user_prompt(payload),
            output_format=SynthesisOutput,
            num_ctx=SYNTHESIS_NUM_CTX,
            max_tokens=SYNTHESIS_MAX_TOKENS,
            temperature=SYNTHESIS_TEMPERATURE,
        )
        parsed: SynthesisOutput = result.output

        logger.info(
            "synthesised article verdict=%s citing %d/%d sources",
            parsed.verdict,
            len(parsed.all_cited_handles()),
            len(payload.sources),
        )
        return LLMResult(output=parsed, usage=result.usage)


def build_ollama_client(base_url: str, timeout_seconds: float) -> AsyncClient:
    """Shared transport for both call sites.

    The timeout is minutes, not seconds: a cold model has to be loaded into
    memory before the first token, and on CPU-only hardware a synthesis turn
    over eight abstracts is genuinely slow. No retry policy — unlike a hosted
    API there is no rate limit or shared-capacity 5xx to ride out, so a failure
    here is a real failure.
    """
    return AsyncClient(host=base_url, timeout=timeout_seconds)
