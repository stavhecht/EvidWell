"""Re-embed cached `sources` whose vectors came from a different model.

Vectors from two models are not comparable. They are not *wrong* in a way
anything can detect — ``mxbai-embed-large`` and ``voyage-4`` are both 1024-d, so
the column accepts either and pgvector computes a cosine distance between them
happily. The number it returns is noise. That is the whole reason this script
exists: a provider switch degrades retrieval silently, and the only signal is
``sources.embedding_model`` disagreeing with the live provider.

``SourceCache`` already handles the rows it touches — ``had_embedding`` compares
the recorded model against ``embedder.model_id`` and re-embeds on a mismatch. So
a paper that gets retrieved again repairs itself. This is for the rest: rows
nobody has fetched since the switch, which is most of them, and which keep
polluting every re-rank they are eligible for.

Run it after changing ``EMBEDDING_PROVIDER`` or the embedding model::

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
text in the same column — the subtler version of the bug this repairs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import func, or_, select, update

from app.config import get_settings
from app.db import dispose_engine, get_session_factory
from app.domain.models import Source
from app.llm.embeddings.base import EmbeddingError
from app.llm.embeddings.factory import build_embedding_provider

#: How many abstracts to embed and write per transaction chunk. The provider
#: batches internally (Ollama at 64); this bounds how much work a failure
#: halfway through throws away, since each chunk is committed as it lands.
CHUNK = 64


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
                select(Source.embedding_model, func.count())
                .group_by(Source.embedding_model)
                .order_by(func.count().desc())
            )
        ).all()

        print(f"live provider: {target} ({embedder.dimension}-d)\n")
        print("cache by recorded model:")
        for model, count in spread:
            mark = "  <- current" if model == target else ""
            print(f"  {count:5d}  {model or 'NULL (never embedded)'}{mark}")

        # IS DISTINCT FROM, so NULL counts as a mismatch: a row that was never
        # embedded is exactly as unusable as one embedded by another model.
        stale = select(Source.id, Source.abstract).where(
            or_(
                Source.embedding_model.is_distinct_from(target),
                Source.embedding.is_(None),
            )
        )
        if args.limit is not None:
            stale = stale.limit(args.limit)
        rows = (await session.execute(stale)).all()

        embeddable = [(r.id, r.abstract) for r in rows if (r.abstract or "").strip()]
        skipped = [r.id for r in rows if not (r.abstract or "").strip()]

        print(f"\n{len(rows)} row(s) not in the live space")
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
        for start in range(0, len(embeddable), CHUNK):
            chunk = embeddable[start : start + CHUNK]
            try:
                vectors = await embedder.embed_documents([text for _id, text in chunk])
            except EmbeddingError as exc:
                # Reported per chunk rather than fatal: a transient provider
                # failure should not discard the chunks that already landed,
                # and re-running picks up exactly what is still stale.
                failures[str(exc)[:120]] += len(chunk)
                continue

            for (source_id, _text), vector in zip(chunk, vectors, strict=True):
                await session.execute(
                    update(Source)
                    .where(Source.id == source_id)
                    .values(embedding=vector, embedding_model=target)
                )
            await session.commit()
            done += len(chunk)
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
