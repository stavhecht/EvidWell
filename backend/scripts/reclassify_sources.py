"""Re-run study-type classification over the cached `sources` rows.

Needed because classification is Python, not SQL: ``classify_study_type``
reads publication types, then falls back to title and abstract heuristics, so
no ``UPDATE ... SET`` can express it and the migration deliberately does not
try. It adds the ``narrative_review`` value; this puts rows into it.

Run after any change to ``PUBLICATION_TYPE_MAP``, ``NEGATIVE_PUBLICATION_TYPES``
or ``TEXT_PATTERNS``. Without it the cache keeps whatever grade it was given on
the day it was fetched, and a classifier fix silently applies only to papers
nobody has retrieved yet — which is the opposite of the ones you want fixed,
since the cached set is exactly what recent articles were built from.

Dry by default::

    python -m scripts.reclassify_sources            # report only
    python -m scripts.reclassify_sources --apply    # write

Protocols are *reported*, never deleted. They should not have been cached at
all — ``RetrieveStage`` now drops them before the cache sees them — but a row
already written may be referenced by ``article_sources``, and deleting it would
break the provenance of a published article to tidy up a classification. They
are listed so a human can decide.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter

from sqlalchemy import select, update

from app.db import dispose_engine, get_session_factory
from app.domain.enums import StudyType
from app.domain.models import Source
from app.evidence.grading import classify_study_type, is_protocol


def _publication_types(raw: str | None) -> list[str]:
    """Split the stored form back into tags.

    ``pubmed.py`` joins them with "; " on the way in, so this is the inverse.
    A provider that supplied none leaves NULL, and classification falls through
    to the text heuristics exactly as it did originally.
    """
    return [part.strip() for part in (raw or "").split(";") if part.strip()]


async def main() -> int:
    """Wraps ``_run`` so the engine is disposed inside the same event loop.

    Disposing from a ``finally`` around ``asyncio.run`` starts a *second* loop
    and asyncpg's connections belong to the first, which surfaces as
    "attached to a different loop" after the work has already succeeded.
    """
    try:
        return await _run()
    finally:
        await dispose_engine()


async def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the changes")
    parser.add_argument("--quiet", action="store_true", help="counts only")
    args = parser.parse_args()

    changes: list[tuple[str, StudyType, StudyType, str]] = []
    protocols: list[tuple[str, str]] = []
    moves: Counter[str] = Counter()

    factory = get_session_factory()
    async with factory() as session:
        rows = (
            await session.execute(
                select(
                    Source.id,
                    Source.title,
                    Source.abstract,
                    Source.raw_study_type,
                    Source.study_type,
                )
            )
        ).all()

        for row in rows:
            if is_protocol(row.raw_study_type, row.title):
                protocols.append((row.id, row.title))

            fresh = classify_study_type(
                _publication_types(row.raw_study_type), row.title, row.abstract
            )
            if fresh is not row.study_type:
                changes.append((row.id, row.study_type, fresh, row.title))
                moves[f"{row.study_type} -> {fresh}"] += 1

        print(f"{len(rows)} cached sources, {len(changes)} would be reclassified\n")
        for move, count in moves.most_common():
            print(f"  {count:4d}  {move}")

        if changes and not args.quiet:
            print("\ndetail:")
            for _id, before, after, title in changes:
                print(f"  {before:>18} -> {after:<18} {title[:58]}")

        if protocols:
            print(f"\n{len(protocols)} cached row(s) are trial protocols and should")
            print("not have been retrieved. Left in place — they may be referenced by")
            print("article_sources, and provenance outranks tidiness:")
            for _id, title in protocols:
                print(f"  {title[:70]}")

        if not args.apply:
            print("\nDry run. Re-run with --apply to write.")
            return 0

        for source_id, _before, after, _title in changes:
            await session.execute(
                update(Source).where(Source.id == source_id).values(study_type=after)
            )
        await session.commit()
        print(f"\nUpdated {len(changes)} row(s).")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
