"""Swappable embedding provider.

Text in, vector out. This is the only non-generative model call in the system
and it exists solely to serve the semantic re-rank in retrieval/rerank.py.

Two things are deliberately part of the interface rather than an implementation
detail:

* ``dimension`` — the vector width is a schema commitment. Changing it means a
  table migration and an HNSW rebuild, so a provider must state it up front and
  startup asserts it matches ``settings.embedding_dim``.
* ``model_id`` — persisted alongside every vector in ``sources.embedding_model``.
  Without it, a provider or model change silently leaves the cache holding
  vectors from two different spaces, and cosine distances between them are
  meaningless. This is the single nastiest failure mode in the retrieval layer,
  and it is invisible: nothing errors, results just quietly get worse.
"""

from __future__ import annotations

from typing import Protocol


class EmbeddingProvider(Protocol):
    @property
    def model_id(self) -> str:
        """Stable identifier persisted with every vector, e.g. 'voyage-3'."""
        ...

    @property
    def dimension(self) -> int:
        """Vector width. Must equal settings.embedding_dim."""
        ...

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed abstracts for storage. Order-preserving.

        Batched by the implementation; callers pass the whole list.
        """
        ...

    async def embed_query(self, text: str) -> list[float]:
        """Embed a claim for search.

        Separate from ``embed_documents`` because several providers (Voyage
        included) apply an asymmetric input type to queries versus documents,
        which measurably improves retrieval. Do not collapse these two methods.
        """
        ...


class EmbeddingError(RuntimeError):
    """Transport or quota failure from the embedding provider."""
