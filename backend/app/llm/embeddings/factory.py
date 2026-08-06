"""Selects the embedding provider from config, and checks its width.

The dimension check lives here rather than at each call site because this is
the one place that knows both the configured width and the live provider. A
mismatch between them is the nastiest failure in the retrieval layer: nothing
errors, cosine distances just become meaningless.
"""

from __future__ import annotations

from app.config import Settings
from app.llm.embeddings.base import EmbeddingError, EmbeddingProvider


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Construct the configured provider and assert its dimension matches.

    Raises:
        EmbeddingError: unknown provider name, or missing API key.
        RuntimeError: the provider's width disagrees with settings.embedding_dim.
    """
    name = settings.embedding_provider.lower()

    if name == "ollama":
        from app.llm.embeddings.ollama import build_ollama_provider

        provider: EmbeddingProvider = build_ollama_provider(
            settings.ollama_base_url,
            settings.ollama_embedding_model,
            settings.ollama_embedding_dim,
            settings.ollama_timeout_seconds,
        )
    elif name == "voyage":
        if not settings.voyage_api_key:
            raise EmbeddingError("VOYAGE_API_KEY is not set")
        from app.llm.embeddings.voyage import build_voyage_provider

        provider = build_voyage_provider(settings.voyage_api_key)
    elif name == "openai":
        if not settings.openai_api_key:
            raise EmbeddingError("OPENAI_API_KEY is not set")
        from app.llm.embeddings.openai import build_openai_provider

        provider = build_openai_provider(settings.openai_api_key)
    else:
        raise EmbeddingError(
            f"unknown embedding_provider {settings.embedding_provider!r}; "
            "expected 'ollama', 'voyage' or 'openai'"
        )

    settings.validate_embedding_dim(provider.dimension)
    return provider
