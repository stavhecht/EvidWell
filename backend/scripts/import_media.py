"""Move media files off local disk and into the `media_objects` table.

One-time, for the store that existed before ``0004_media_objects.sql``. Run it
once after that migration and the directory can be deleted::

    python -m scripts.import_media                  # report only
    python -m scripts.import_media --apply          # write
    python -m scripts.import_media --source /path   # non-default directory

Idempotent, because the key is the digest of the bytes: a second run finds
everything already present and writes nothing. That also makes it safe to run
against a partially-imported store.

**The filename is not trusted, and neither is the path.** Every file is sniffed
and re-digested by ``prepare_image``, exactly as an upload is, and a file whose
name disagrees with its own SHA-256 is reported rather than imported under
either value. That sounds paranoid for a directory we wrote ourselves, and it
is the point: the digest is what article documents reference, so importing a
file under the wrong one would produce a row nothing can reach and leave the
article's ``src`` broken with a row sitting in the table looking correct.

Nothing is deleted. Files stay where they are until a human removes the
directory, so a failed import costs a re-run rather than the images.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from pathlib import Path

from app.db import dispose_engine, get_session_factory
from app.services.media import (
    UnsupportedMediaError,
    media_digests,
    prepare_image,
    store_image,
)

#: The layout the old on-disk store wrote: a two-character shard directory, and
#: a filename of the remaining 62 hex characters plus the extension.
_DEFAULT_SOURCE = Path("var/media")


def _expected_digest(path: Path) -> str | None:
    """The digest this file's *path* claims, or None if it is not that shape."""
    shard, name = path.parent.name, path.stem
    if len(shard) != 2 or len(name) != 62:
        return None
    candidate = shard + name
    return candidate if all(c in "0123456789abcdef" for c in candidate) else None


async def main() -> int:
    """Wraps ``_run`` so the engine is disposed inside the same event loop.

    Same reason as ``reclassify_sources``: disposing from a ``finally`` around
    ``asyncio.run`` starts a second loop, and asyncpg's connections belong to
    the first.
    """
    try:
        return await _run()
    finally:
        await dispose_engine()


async def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the rows")
    parser.add_argument(
        "--source",
        type=Path,
        default=_DEFAULT_SOURCE,
        help=f"directory holding the old store (default: {_DEFAULT_SOURCE})",
    )
    args = parser.parse_args()

    source: Path = args.source.expanduser()
    if not source.is_dir():
        print(f"No such directory: {source}")
        print("Nothing to import — if the store was never on disk, that is expected.")
        return 0

    files = sorted(p for p in source.rglob("*") if p.is_file())
    if not files:
        print(f"{source} holds no files. Nothing to import.")
        return 0

    factory = get_session_factory()
    async with factory() as session:
        already = await media_digests(session)

        importable: list[tuple[Path, bytes, str, int]] = []
        skipped: list[tuple[Path, str]] = []
        present = 0
        kinds: Counter[str] = Counter()

        for path in files:
            data = path.read_bytes()
            try:
                prepared = prepare_image(data)
            except UnsupportedMediaError as exc:
                # Reported, never imported. The `.DS_Store` and `.part` files
                # this directory has collected land here, and so would anything
                # genuinely wrong.
                skipped.append((path, str(exc)))
                continue

            claimed = _expected_digest(path)
            if claimed is not None and claimed != prepared.digest:
                skipped.append(
                    (
                        path,
                        f"path claims {claimed[:12]}… but the bytes hash to "
                        f"{prepared.digest[:12]}… — not imported under either",
                    )
                )
                continue

            if prepared.digest in already:
                present += 1
                continue

            kinds[prepared.extension] += 1
            importable.append((path, data, prepared.digest, len(data)))

        total_bytes = sum(size for _p, _d, _dig, size in importable)
        print(f"{len(files)} file(s) under {source}")
        print(f"  {present:4d} already in media_objects")
        print(f"  {len(importable):4d} to import ({total_bytes / 1024:.0f} kB)")
        for extension, count in sorted(kinds.items()):
            print(f"         {count:4d}  .{extension}")

        if skipped:
            print(f"\n  {len(skipped)} skipped:")
            for path, why in skipped:
                print(f"    {path.name[:44]:<44}  {why}")

        if not importable:
            print("\nNothing to do.")
            return 0

        if not args.apply:
            print("\nDry run. Re-run with --apply to write.")
            return 0

        for _path, data, _digest, _size in importable:
            # Through `store_image` rather than a bulk INSERT: it is the one
            # definition of what a stored row looks like, and an import that
            # wrote rows a different way would be the second definition.
            await store_image(data, session=session)
        await session.commit()

        print(f"\nImported {len(importable)} file(s).")
        print(f"{source} is no longer read by the application and can be deleted.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
