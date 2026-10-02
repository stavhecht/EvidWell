"""The embedding provider, cached per text and recorded per batch.

Embeddings are a pure function of (model, text), so caching them changes no
result — it only makes the second run of a case skip the ~100 abstracts the
first one embedded. Vectors are stored as float32 in base64: a 1024-d vector is
5.5 KB rather than the ~20 KB its JSON list would take.
"""

from __future__ import annotations

import base64
import time

import numpy as np

from app.llm.embeddings.base import EmbeddingError, EmbeddingProvider
from evaluation.harness.cassette import Cassette, stable_key
from evaluation.harness.trace import TraceRecorder
from evaluation.schema import FaultSpec


def _encode(vector: list[float]) -> str:
    return base64.b64encode(np.asarray(vector, dtype=np.float32).tobytes()).decode()


def _decode(blob: str) -> list[float]:
    return np.frombuffer(base64.b64decode(blob), dtype=np.float32).astype(float).tolist()


class CachingEmbedder:
    def __init__(
        self,
        inner: EmbeddingProvider | None,
        cassette: Cassette,
        mode: str,
        recorder: TraceRecorder | None,
        fault: FaultSpec | None = None,
    ) -> None:
        self._inner = inner
        self._cassette = cassette
        self._mode = mode
        self._recorder = recorder
        self._fault = fault

    @property
    def model_id(self) -> str:
        return self._inner.model_id if self._inner else "unavailable"

    @property
    def dimension(self) -> int:
        return self._inner.dimension if self._inner else 0

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._embed(texts, "document")

    async def embed_query(self, text: str) -> list[float]:
        return (await self._embed([text], "query"))[0]

    async def _embed(self, texts: list[str], kind: str) -> list[list[float]]:
        started = time.monotonic()
        operation = f"embed_{kind}"
        if self._fault is not None:
            self._note(
                operation, len(texts), 0, started, status="fault", error="simulated outage"
            )
            raise EmbeddingError("embedding provider unreachable (eval fault)")

        keys = [stable_key("emb", self.model_id, kind, text) for text in texts]
        vectors: list[list[float] | None] = [None] * len(texts)
        if self._mode != "live":
            for index, key in enumerate(keys):
                hit = self._cassette.get("emb", key)
                if hit is not None:
                    vectors[index] = _decode(hit[0])
        missing = [index for index, vector in enumerate(vectors) if vector is None]
        if missing and self._mode == "replay":
            self._note(
                operation,
                len(texts),
                len(texts) - len(missing),
                started,
                status="cassette_miss",
            )
            raise EmbeddingError("cassette miss (replay mode)")
        if missing:
            if self._inner is None:
                raise EmbeddingError("no embedding provider is configured")
            batch = [texts[index] for index in missing]
            try:
                if kind == "query":
                    fresh = [await self._inner.embed_query(batch[0])]
                else:
                    fresh = await self._inner.embed_documents(batch)
            except Exception as exc:
                self._note(operation, len(texts), 0, started, status="error", error=str(exc))
                raise
            for index, vector in zip(missing, fresh, strict=True):
                vectors[index] = vector
                self._cassette.put("emb", keys[index], _encode(vector))
        self._note(operation, len(texts), len(texts) - len(missing), started)
        return [vector for vector in vectors if vector is not None]

    def _note(
        self,
        operation: str,
        count: int,
        cached: int,
        started: float,
        *,
        status: str = "ok",
        error: str | None = None,
    ) -> None:
        if self._recorder is None:
            return
        latency = round((time.monotonic() - started) * 1000, 1)
        self._recorder.record(
            tool="embeddings",
            operation=operation,
            request={"texts": count, "cached": cached},
            status=status,
            error=error,
            cached=count > 0 and cached == count,
            result_count=count,
            latency_ms=latency,
            # A cached batch has no live latency to report; a partial one
            # reports what the live remainder cost.
            live_latency_ms=None if cached == count else latency,
            model=self.model_id,
        )
