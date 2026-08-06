"""Selects the generative provider from config.

The mirror of ``llm/embeddings/factory.py``, and the only place that knows which
concrete client backs the two Protocols in ``llm/base.py``. Everything
downstream — stages, orchestrator, tests — depends on the Protocols, so swapping
providers is an env var:

    LLM_PROVIDER=ollama      # local, free; the current default
    LLM_PROVIDER=anthropic   # hosted Claude; what real deployment runs

Both paths stay live and both are exercised by the same call sites. The
Anthropic client is not commented-out code waiting to be revived — it is one
setting away, which is the point.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.llm.anthropic_client import (
    AnthropicExtractionClient,
    AnthropicSynthesisClient,
    build_anthropic_client,
)
from app.llm.base import ExtractionClient, LLMError, SynthesisClient
from app.llm.ollama_client import (
    OllamaExtractionClient,
    OllamaSynthesisClient,
    build_ollama_client,
)

logger = logging.getLogger(__name__)


def build_generative_clients(
    settings: Settings,
) -> tuple[ExtractionClient, SynthesisClient]:
    """Construct the extraction and synthesis clients for the configured provider.

    Both share one transport, as they did before this seam existed: two clients,
    one connection pool.

    Raises:
        LLMError: unknown provider name, or a hosted provider with no API key.
    """
    name = settings.llm_provider.lower()

    if name == "ollama":
        client = build_ollama_client(
            settings.ollama_base_url, settings.ollama_timeout_seconds
        )
        logger.info(
            "generative provider: ollama at %s (extraction=%s, synthesis=%s)",
            settings.ollama_base_url,
            settings.ollama_extraction_model,
            settings.ollama_synthesis_model,
        )
        return (
            OllamaExtractionClient(client, settings.ollama_extraction_model),
            OllamaSynthesisClient(client, settings.ollama_synthesis_model),
        )

    if name == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError("ANTHROPIC_API_KEY is not set (llm_provider='anthropic')")
        anthropic_client = build_anthropic_client(settings.anthropic_api_key)
        logger.info(
            "generative provider: anthropic (extraction=%s, synthesis=%s)",
            settings.extraction_model,
            settings.synthesis_model,
        )
        return (
            AnthropicExtractionClient(anthropic_client, settings.extraction_model),
            AnthropicSynthesisClient(anthropic_client, settings.synthesis_model),
        )

    raise LLMError(
        f"unknown llm_provider {settings.llm_provider!r}; "
        "expected 'ollama' or 'anthropic'"
    )
