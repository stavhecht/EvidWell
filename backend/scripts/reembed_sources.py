"""Re-chunk and re-embed cached `sources` whose chunks are out of date.

Out of date means embedded by a different model, or cut with different chunk
settings (``retrieval/chunking.py::CHUNK_SETTINGS``).

Vectors from two models are not comparable. They are not *wrong* in a way
anything can detect — ``mxbai-embed-large`` and ``voyage-4`` are both 1024-d, so
the column accepts either and pgvector computes a cosine distance between them
happily. The number it returns is noise. That is the whole reason this script
exists: a provider switch degrades retrieval silently, and the only signal is
``sources.embedding_model`` disagreeing with the live provider.

``SourceCache`` already handles the rows it touches — ``had_embedding`` compares
the recorded model and chunk settings against the live ones and re-makes the
chunks on a mismatch. So a paper that gets retrieved again repairs itself. This
is for the rest: rows nobody has fetched since the change, which is most of
them, and which keep polluting every re-rank they are eligible for.

Run it after changing ``EMBEDDING_PROVIDER``, the embedding model, or the chunk
size, and once after migrations 0002 and 0003, which moved vectors into
overlapping chunks (``source_chunks``) and then changed their size::

    python -m scripts.reembed_sources            # report only
    python -m scripts.reembed_sources --apply    # write

Dry by default. The dry run costs nothing and makes no provider call — it only
counts what would be re-embedded, so it is safe against a hosted provider.

**The model is not a flag here.** The provider comes from
``build_embedding_provider(settings)``, the same factory the pipeline uses, so
this script cannot re-embed into a space the pipeline is not reading from.
Point the settings at the provider you want *first*, then run this.

Rows with no abstract are reported and skipped: ``ensure_embeddings`` embeds the
abstract alone, so there is nothing to embed and a title would be a different
text in the same space — the subtler version of the bug this repairs.

Chunks are written by ``retrieval/cache.py::write_chunks``, the same function
the pipeline uses, so a re-embedded paper is chunked exactly like a new one.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import func, select

from app.config import get_settings
from app.db import dispose_engine, get_session_factory
from app.domain.models import Source
from app.llm.embeddings.base import EmbeddingError
from app.llm.embeddings.factory import build_embedding_provider
from app.retrieval.cache import write_chunks
from app.retrieval.chunking import CHUNK_SETTINGS

#: How many abstracts to embed and write per transaction. The provider batches
#: internally (Ollama at 64); this bounds how much work a failure halfway
#: through throws away, since each batch is committed as it lands.
BATCH = 64


async def main() -> int:
    """Wraps ``_run`` so the engine is disposed inside the same event loop.

    Same reason as ``reclassify_sources``: disposing from a ``finally`` around
    ``asyncio.run`` starts a second loop and asyncpg's connections belong to
    the first.
    """
    try:
        return await _run()
    finally:
        await dispose_engine()


async def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the vectors")
    parser.add_argument(
        "--limit", type=int, default=None, help="stop after N rows (for a trial run)"
    )
    args = parser.parse_args()

    settings = get_settings()
    embedder = build_embedding_provider(settings)
    target = embedder.model_id

    factory = get_session_factory()
    async with factory() as session:
        spread = (
            await session.execute(
                select(Source.embedding_model, Source.chunk_settings, func.count())
                .group_by(Source.embedding_model, Source.chunk_settings)
                .order_by(func.count().desc())
            )
        ).all()

        print(f"live provider: {target} ({embedder.dimension}-d), chunks {CHUNK_SETTINGS}\n")
        print("cache by recorded model and chunk settings:")
        for model, chunking, count in spread:
            mark = "  <- current" if (model, chunking) == (target, CHUNK_SETTINGS) else ""
            label = f"{model}, {chunking}" if model else "NULL (never embedded)"
            print(f"  {count:5d}  {label}{mark}")

        # IS DISTINCT FROM, so NULL counts as a mismatch: a row that was never
        # embedded is exactly as unusable as one embedded by another model.
        stale = select(Source.id, Source.abstract).where(
            Source.embedding_model.is_distinct_from(target)
            | Source.chunk_settings.is_distinct_from(CHUNK_SETTINGS)
        )
        if args.limit is not None:
            stale = stale.limit(args.limit)
        rows = (await session.execute(stale)).all()

        embeddable = [(r.id, r.abstract) for r in rows if (r.abstract or "").strip()]
        skipped = [r.id for r in rows if not (r.abstract or "").strip()]

        print(f"\n{len(rows)} row(s) out of date")
        print(f"  {len(embeddable):5d} will be re-embedded")
        if skipped:
            print(f"  {len(skipped):5d} skipped — no abstract to embed")

        if not embeddable:
            print("\nNothing to do.")
            return 0

        if not args.apply:
            print("\nDry run. No provider call was made.")
            print("Re-run with --apply to re-embed.")
            return 0

        done = 0
        failures: Counter[str] = Counter()
        for start in range(0, len(embeddable), BATCH):
            batch = dict(embeddable[start : start + BATCH])
            try:
                await write_chunks(session, embedder, batch)
            except EmbeddingError as exc:
                # Reported per batch rather than fatal: a transient provider
                # failure should not discard the batches that already landed,
                # and re-running picks up exactly what is still stale.
                # write_chunks embeds before it writes, so nothing is half-written.
                failures[str(exc)[:120]] += len(batch)
                continue

            await session.commit()
            done += len(batch)
            print(f"  re-embedded {done}/{len(embeddable)}")

        print(f"\nRe-embedded {done} row(s) into {target}.")
        if failures:
            print(f"{sum(failures.values())} row(s) failed and are still stale:")
            for message, count in failures.most_common():
                print(f"  {count:5d}  {message}")
            print("Re-run to retry them.")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
