"""Ollama embedding provider — local and free, the current default.

Same contract as Voyage and OpenAI: text in, vector out, order preserved. Runs
against a local ``ollama serve``, so there is no key and no per-token cost while
the pipeline is being built out.

Two things carry over from ``openai.py`` and matter more here, because switching
back to a hosted provider is the *expected* end state:

* **Vectors from different models are not comparable.** ``model_id`` is
  namespaced (``ollama/mxbai-embed-large``) and persisted with every row, so a
  provider change cannot silently mix two vector spaces in ``sources`` — the
  cache re-embeds anything whose recorded model differs. The backfill itself is
  still your job.
* **The width is a schema commitment.** ``mxbai-embed-large`` is 1024-d, chosen
  to match the existing ``EMBEDDING_DIM`` and therefore the applied migration's
  ``vector(1024)`` column — so you can embed locally today and move to Voyage
  later without a migration. Picking a 768-d model (``nomic-embed-text``)
  instead is a migration plus a full re-embed, not a config edit.
"""

from __future__ import annotations

import logging

import httpx
from ollama import AsyncClient, ResponseError

from app.llm.embeddings.base import EmbeddingError

logger = logging.getLogger(__name__)

OLLAMA_MODEL = "mxbai-embed-large"

#: Output widths for the embedding models Ollama ships, keyed by name without
#: its tag. A model outside this table falls back to the declared
#: ``settings.ollama_embedding_dim`` — which reduces the startup check to
#: comparing two config values rather than asserting against the provider, so
#: add the model here instead of relying on the fallback.
KNOWN_DIMENSIONS: dict[str, int] = {
    "mxbai-embed-large": 1024,
    "bge-m3": 1024,
    "bge-large": 1024,
    "snowflake-arctic-embed": 1024,
    "nomic-embed-text": 768,
    "embeddinggemma": 768,
    "all-minilm": 384,
}

#: (document prefix, query prefix). Several open embedding models are trained
#: with instruction prefixes and lose measurable retrieval quality without them.
#: This is the same query/document asymmetry Voyage expresses through
#: ``input_type``, and the reason ``EmbeddingProvider`` keeps the two methods
#: separate.
INSTRUCTION_PREFIXES: dict[str, tuple[str, str]] = {
    "nomic-embed-text": ("search_document: ", "search_query: "),
    "mxbai-embed-large": (
        "",
        "Represent this sentence for searching relevant passages: ",
    ),
}

# The local server documents no batch cap; this bounds request size and memory
# on modest hardware rather than satisfying an API limit.
MAX_BATCH_SIZE = 64


def _base_name(model: str) -> str:
    """``mxbai-embed-large:335m`` -> ``mxbai-embed-large``."""
    return model.split(":", 1)[0]


class OllamaEmbeddingProvider:
    def __init__(self, client: AsyncClient, model: str, dimension: int) -> None:
        self._client = client
        self._model = model
        self._dimension = dimension
        self._doc_prefix, self._query_prefix = INSTRUCTION_PREFIXES.get(
            _base_name(model), ("", "")
        )

    @property
    def model_id(self) -> str:
        """Namespaced, so a local vector is never mistaken for a hosted one."""
        return f"ollama/{self._model}"

    @property
    def dimension(self) -> int:
        return self._dimension

    async def _embed(self, inputs: list[str]) -> list[list[float]]:
        """One ``embed`` round trip."""
        try:
            response = await self._client.embed(model=self._model, input=inputs)
        except ResponseError as exc:
            if exc.status_code == 404:
                raise EmbeddingError(
                    f"ollama does not have model {self._model!r} — run "
                    f"`ollama pull {self._model}`"
                ) from exc
            raise EmbeddingError(f"ollama embed failed: {exc}") from exc
        except (ConnectionError, httpx.HTTPError) as exc:
            # A refused connection surfaces as a builtin ConnectionError and a
            # timeout as an httpx error; neither is a ResponseError.
            raise EmbeddingError(
                f"ollama embed failed: {exc} — is `ollama serve` running?"
            ) from exc

        # The SDK types these as Sequence[Sequence[float]]; materialise a list
        # of lists, which is what pgvector's binding expects.
        return [list(vector) for vector in response.embeddings]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed abstracts for storage.

        Order preservation is not incidental — callers zip the result against
        the candidate list, so a reordered response would attach every embedding
        to the wrong paper. Ollama returns one vector per input in order;
        batches are concatenated in input order and the total is asserted.
        """
        if not texts:
            return []

        vectors: list[list[float]] = []
        for start in range(0, len(texts), MAX_BATCH_SIZE):
            batch = texts[start : start + MAX_BATCH_SIZE]
            vectors.extend(
                await self._embed([f"{self._doc_prefix}{text}" for text in batch])
            )

        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"ollama returned {len(vectors)} vectors for {len(texts)} inputs; "
                "refusing to return a misaligned batch"
            )
        self._assert_width(vectors)
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        """Embed a claim for search, with the model's query prefix applied."""
        vectors = await self._embed([f"{self._query_prefix}{text}"])
        if not vectors:
            raise EmbeddingError("ollama embed_query returned no vector")
        self._assert_width(vectors)
        return vectors[0]

    def _assert_width(self, vectors: list[list[float]]) -> None:
        """Catch a wrong-width model before pgvector does.

        Cheap, and it is the one check that turns "unknown model, trusting the
        configured dimension" back into a real assertion — at the first call
        rather than at the first write.
        """
        for vector in vectors:
            if len(vector) != self._dimension:
                raise EmbeddingError(
                    f"ollama model {self._model!r} emitted a {len(vector)}-d vector, "
                    f"expected {self._dimension}; the vector(N) column and "
                    "EMBEDDING_DIM must match the model"
                )


def build_ollama_provider(
    base_url: str, model: str, fallback_dimension: int, timeout_seconds: float
) -> OllamaEmbeddingProvider:
    """Construct the provider, resolving the model's width from the table above."""
    dimension = KNOWN_DIMENSIONS.get(_base_name(model))
    if dimension is None:
        dimension = fallback_dimension
        logger.warning(
            "unknown ollama embedding model %r; trusting ollama_embedding_dim=%d. "
            "Add it to KNOWN_DIMENSIONS so startup can check the width for real.",
            model,
            fallback_dimension,
        )

    return OllamaEmbeddingProvider(
        AsyncClient(host=base_url, timeout=timeout_seconds), model, dimension
    )
