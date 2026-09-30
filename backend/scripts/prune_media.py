"""Delete stored images that no article references any more.

    python -m scripts.prune_media                 # dry run: what would go
    python -m scripts.prune_media --apply         # delete
    python -m scripts.prune_media --grace-days 30

Image bytes live in ``media_objects``, keyed by digest and deliberately not
hung off ``articles`` (see migrations/0001_initial.sql). The cost of that design
is that nothing deletes a picture when the article stops pointing at it: every
Regenerate press stores new frames and leaves the old ones behind, and a
reviewer who swaps an uploaded image leaves the first upload. Measured
2026-09-30: 6 of 23 stored images (380 KB) were referenced by nothing.

A row is deleted only when **no column of any article** mentions its path —
``original_content``, ``edited_content``, ``generated_imagery`` (the cover
lives only there) and ``card_image`` — and it is older than the grace period.
The grace period covers the one legitimate unreferenced state: a reviewer has
uploaded an image and the autosave that puts it in ``edited_content`` has not
landed yet. Rejected and validation-failed articles count as references too:
their documents are kept, and a picture they show must keep resolving.

Dry by default, like every maintenance script here.
"""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import cast, delete, select
from sqlalchemy.types import Text

from app.db import dispose_engine, get_session_factory
from app.domain.models import Article, MediaObject

#: A stored path's shard and digest tail, anywhere in a JSON document.
_PATH_RE = re.compile(r"/api/media/([0-9a-f]{2})/([0-9a-f]{62})\.")


def referenced_digests(documents: list[str]) -> set[str]:
    """Every digest whose path appears in any of the given texts."""
    found: set[str] = set()
    for text in documents:
        for shard, tail in _PATH_RE.findall(text):
            found.add(shard + tail)
    return found


async def run(*, apply: bool, grace_days: int) -> int:
    factory = get_session_factory()
    cutoff = datetime.now(UTC) - timedelta(days=grace_days)
    async with factory() as session:
        rows = await session.execute(
            select(
                cast(Article.original_content, Text),
                cast(Article.edited_content, Text),
                cast(Article.generated_imagery, Text),
                Article.card_image,
            )
        )
        documents = [text for row in rows for text in row if text]
        referenced = referenced_digests(documents)

        stored = (
            await session.execute(
                select(MediaObject.digest, MediaObject.extension, MediaObject.created_at)
            )
        ).all()
        orphans = [
            (digest, extension)
            for digest, extension, created in stored
            if digest not in referenced and created < cutoff
        ]
        recent = sum(
            1 for digest, _, created in stored if digest not in referenced and created >= cutoff
        )

        print(
            f"{len(stored)} stored, {len(referenced & {d for d, _, _ in stored})} referenced, "
            f"{len(orphans)} unreferenced and older than {grace_days} days"
            + (f", {recent} unreferenced but still within the grace period" if recent else "")
        )
        for digest, extension in orphans:
            print(f"  {'delete' if apply else 'would delete'} {digest[:12]}….{extension}")

        if apply and orphans:
            await session.execute(
                delete(MediaObject).where(MediaObject.digest.in_([d for d, _ in orphans]))
            )
            await session.commit()
            print(f"deleted {len(orphans)}")
        elif orphans:
            print("dry run; pass --apply to delete")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    parser.add_argument("--grace-days", type=int, default=7)
    args = parser.parse_args()

    async def _main() -> int:
        try:
            return await run(apply=args.apply, grace_days=args.grace_days)
        finally:
            await dispose_engine()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
