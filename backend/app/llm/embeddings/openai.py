"""OpenAI embedding provider — the documented alternative.

Exists to keep ``EmbeddingProvider`` honest: an interface with one
implementation is an assumption, not an abstraction.

**Switching providers on a populated cache requires a re-embed.** Vectors from
different models occupy different spaces; cosine distance between them is
noise. The failure is silent — no error, results just quietly degrade. The
cache guards against reuse by comparing ``sources.embedding_model`` to the
active provider, but the backfill itself is your job. Note also that
``text-embedding-3-large`` is 3072-dimensional against Voyage's 1024, so
switching is a schema migration as well.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.llm.embeddings.base import EmbeddingError

# `openai` is an optional extra (pip install -e ".[openai]") and is normally
# NOT installed — the default stack is local Ollama. So every reference to it
# here is guarded, and the type-checker ignores mean "absent by design", not
# "unresolved import". Importing this module must stay free: the embeddings
# factory imports it lazily, but selecting Ollama or Voyage cannot require the
# OpenAI package to be present.
if TYPE_CHECKING:
    from openai import AsyncOpenAI  # ty: ignore[unresolved-import]

try:  # pragma: no cover - trivial import guard
    from openai import OpenAIError  # ty: ignore[unresolved-import]
except ModuleNotFoundError:  # pragma: no cover

    class OpenAIError(Exception):  # type: ignore[no-redef]
        """Placeholder so `except` clauses below stay valid without openai."""

logger = logging.getLogger(__name__)

OPENAI_MODEL = "text-embedding-3-large"
OPENAI_DIMENSION = 3072

MAX_BATCH_SIZE = 256


class OpenAIEmbeddingProvider:
    def __init__(self, client: AsyncOpenAI, model: str = OPENAI_MODEL) -> None:
        self._client = client
        self._model = model

    @property
    def model_id(self) -> str:
        return self._model

    @property
    def dimension(self) -> int:
        return OPENAI_DIMENSION

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[start : start + MAX_BATCH_SIZE]
            try:
                response = await self._client.embeddings.create(
                    input=batch, model=self._model
                )
            except OpenAIError as exc:
                raise EmbeddingError(f"openai embed_documents failed: {exc}") from exc
            # OpenAI does not guarantee response order — sort by index before
            # extending, or vectors attach to the wrong papers.
            ordered = sorted(response.data, key=lambda item: item.index)
            vectors.extend(item.embedding for item in ordered)

        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"openai returned {len(vectors)} vectors for {len(texts)} inputs"
            )
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """OpenAI has no query/document asymmetry; same model both ways."""
        try:
            response = await self._client.embeddings.create(input=[text], model=self._model)
        except OpenAIError as exc:
            raise EmbeddingError(f"openai embed_query failed: {exc}") from exc
        # Coerced like the Voyage and Ollama providers do, so the vector handed
        # to pgvector has one consistent element type whichever provider is live.
        return [float(value) for value in response.data[0].embedding]


def build_openai_provider(api_key: str) -> OpenAIEmbeddingProvider:
    try:
        from openai import AsyncOpenAI  # ty: ignore[unresolved-import]
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise EmbeddingError(
            "embedding_provider='openai' requires the optional dependency: "
            'pip install -e ".[openai]"'
        ) from exc
    return OpenAIEmbeddingProvider(AsyncOpenAI(api_key=api_key))
