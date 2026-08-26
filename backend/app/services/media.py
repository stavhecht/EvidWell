"""Reviewer-uploaded media: what the store accepts, and what a document may cite.

Two rules, which are one rule seen from either end:

* **Bytes enter the store only if their content says they are an image.** The
  client's ``Content-Type`` and filename are read and discarded — both are
  attacker-controlled strings, and this store writes into a directory that is
  served straight back over HTTP. The first bytes of the file decide.
* **A document may reference only media this store wrote**, plus a YouTube
  video id. A remote ``https://`` image, a ``javascript:`` URL, a hand-typed
  iframe src: all refused, at save time and again at approve time.

SVG is deliberately absent from the allowlist. An SVG is a script-bearing
document; one served from our own origin and embedded in a published article is
stored XSS against every reader of that article. PNG, JPEG, GIF and WebP cannot
carry script, which is the whole reason the list is those four.

Storage is content-addressed — the filename is the SHA-256 of the bytes. The
same image uploaded twice is one file, two uploads can never collide, and no
fragment of a client-supplied string reaches the filesystem, so there is
nothing to traverse with.

Local disk is the store for now, and the seam is deliberately narrow:
``store_image()`` returns the path a document should hold, so the S3 version is
this module and nothing else (DESIGN.md §11 — deployment is deferred).
Orphan collection is deferred too: an image dropped from a draft leaves its
file behind. The files are small, immutable and content-addressed, which makes
a sweep over ``articles.original_content``/``edited_content`` a job that can be
written correctly later rather than one that has to be got right now.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.services.tiptap import iter_nodes

#: Where stored media is served from. Under ``/api`` on purpose: the frontend
#: dev server already proxies that prefix and any deployment already routes it,
#: so uploaded images need no new rule in either place. Article documents hold
#: this path verbatim, so changing it is a rewrite of stored JSON.
MEDIA_URL_PREFIX = "/api/media"

#: Magic bytes → extension. Order matters only in that each prefix is checked
#: whole; none of the four is a prefix of another.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
)

#: Longest signature we need to see before deciding. WebP needs 12.
SNIFF_BYTES = 12

#: Extension → the type ``StaticFiles`` will serve the file as. Exists so the
#: upload response can report what the bytes *were*, rather than echoing back
#: the Content-Type the client claimed and which nothing checked.
CONTENT_TYPES: dict[str, str] = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}

#: The exact shape ``store_image`` produces: a two-character shard, the
#: remaining 62 hex characters of the digest, one of four extensions. Anchored,
#: with no ``.`` or ``/`` permitted inside, so ``/api/media/../../etc/passwd``
#: does not match and neither does a same-origin path pointing anywhere else.
MEDIA_SRC_RE = re.compile(
    rf"^{re.escape(MEDIA_URL_PREFIX)}/[0-9a-f]{{2}}/[0-9a-f]{{62}}\.(?:png|jpg|gif|webp)$"
)

#: YouTube video ids are exactly 11 characters of an unpadded base64url
#: alphabet. Documents store the **id**, never a URL: the renderer builds the
#: embed src from it, so there is no string a reviewer can supply that lands
#: unexamined in an iframe.
YOUTUBE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

#: How a media block sits in the prose. ``left`` and ``right`` float and the
#: text wraps down the other side; ``none`` is a block. Mirrors ``MediaAlign``
#: in ``frontend/src/lib/media.ts``.
#:
#: An enum rather than a CSS string is the whole point. The renderer turns one
#: of these three words into a class it already ships; nothing a reviewer
#: supplies becomes style on a published page.
MEDIA_ALIGNMENTS = frozenset({"none", "left", "right"})

#: Width is a percentage of the prose column, never pixels — the editor's
#: column and the article's differ, and a percentage is the only unit that
#: means the same in both. The floor stops a picture being shrunk to a speck.
MEDIA_MIN_WIDTH = 20
MEDIA_MAX_WIDTH = 100


class UnsupportedMediaError(ValueError):
    """Uploaded bytes are not one of the four accepted image formats."""


class UnsafeMediaError(ValueError):
    """A document references media this system did not store.

    Surfaces as HTTP 409 through ``ReviewError``. Never softened to a warning
    and never fixed by silently dropping the node — a reviewer whose image
    vanished without explanation has no way to tell that from a bug.
    """


def sniff_image(data: bytes) -> str | None:
    """The file extension the bytes actually are, or None.

    Returns the extension rather than a MIME type because the extension is what
    gets written to disk, and deriving it from anything the client said is the
    mistake this function exists to prevent.
    """
    for signature, extension in _SIGNATURES:
        if data.startswith(signature):
            return extension
    # WebP is a RIFF container; the format tag sits at offset 8.
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def ensure_media_root(root: Path) -> Path:
    """Create the store directory and return it resolved.

    Called at app startup: ``StaticFiles`` refuses to mount a directory that
    does not exist, and a fresh clone has never uploaded anything.
    """
    resolved = root.expanduser().resolve()
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


@dataclass(frozen=True, slots=True)
class StoredImage:
    """A file now in the store. ``src`` is what the document holds."""

    src: str
    content_type: str
    size: int


def store_image(data: bytes, *, root: Path) -> StoredImage:
    """Write image bytes to the store and return the path a document may hold.

    Raises:
        UnsupportedMediaError: the bytes are not PNG, JPEG, GIF or WebP.
    """
    extension = sniff_image(data[:SNIFF_BYTES])
    if extension is None:
        raise UnsupportedMediaError(
            "not a PNG, JPEG, GIF or WebP image (the file's own contents were "
            "checked, not its name)"
        )

    digest = hashlib.sha256(data).hexdigest()
    relative = f"{digest[:2]}/{digest[2:]}.{extension}"
    target = root / relative

    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        # Write beside the target and rename. Two reviewers uploading the same
        # image at once is the normal case for content addressing, and a
        # half-written file at a path an article already points at is a broken
        # published image. os.replace is atomic within a filesystem.
        scratch = target.with_name(f"{target.name}.{uuid4().hex}.part")
        scratch.write_bytes(data)
        os.replace(scratch, target)

    return StoredImage(
        src=f"{MEDIA_URL_PREFIX}/{relative}",
        content_type=CONTENT_TYPES[extension],
        size=len(data),
    )


def image_node(src: str, alt: str) -> dict[str, Any]:
    """The document node for a stored image, at full column width.

    Lives here rather than in ``tiptap.py`` for two reasons. This module owns
    ``MEDIA_SRC_RE``, ``MEDIA_ALIGNMENTS`` and the width bounds, so the node it
    builds is exactly the shape it validates a few lines below — the two cannot
    drift apart while they are in one file. And ``media.py`` imports
    ``tiptap.py``, so the dependency in the other direction would be a cycle.

    Used by the pipeline to place a generated lead image; a reviewer's uploads
    are built client-side by ``useMediaInsert.ts`` to the same shape. Both are
    then re-checked by ``assert_media_is_ours``, which is the point: nothing is
    trusted because of where it came from.
    """
    return {
        "type": "image",
        "attrs": {
            "src": src,
            "alt": alt,
            # Explicit rather than omitted. Absent is valid — every article
            # written before media could be laid out has neither attribute —
            # but a node this system writes should say what it means, and the
            # editor's controls read these two to render their initial state.
            "width": MEDIA_MAX_WIDTH,
            "align": "none",
        },
    }


def assert_media_is_ours(doc: dict[str, Any]) -> None:
    """Every media node points at something we control, laid out how we allow.

    Walks the whole tree, not just where media is meant to sit. A check that
    only looks in the expected place is not a check.

    Raises:
        UnsafeMediaError: naming the node and the offending value, because the
            reviewer needs to know which block to remove.
    """
    for node in iter_nodes(doc):
        node_type = node.get("type")
        if node_type not in ("image", "youtube"):
            continue

        attrs = node.get("attrs")
        attrs = attrs if isinstance(attrs, dict) else {}
        _assert_layout_is_allowed(attrs)

        if node_type == "image":
            src = attrs.get("src")
            if not isinstance(src, str) or not MEDIA_SRC_RE.match(src):
                raise UnsafeMediaError(
                    f"an image in this draft points at {_describe(src)}, which was "
                    "not uploaded through the console. Remove it and add the image "
                    "with the Image button — pasted or linked images are not stored, "
                    "so they would break or track readers."
                )
        else:
            video_id = attrs.get("videoId")
            if not isinstance(video_id, str) or not YOUTUBE_ID_RE.match(video_id):
                raise UnsafeMediaError(
                    f"a video block in this draft carries {_describe(video_id)} "
                    "instead of a YouTube video id. Remove it and add the video "
                    "with the Video button."
                )


def _assert_layout_is_allowed(attrs: dict[str, Any]) -> None:
    """``width`` and ``align`` are within the range the renderer can express.

    **Absent is valid.** Every article written before media could be laid out
    has neither attribute, and those documents must keep saving — a check that
    invalidates existing rows is a migration wearing a validator's clothes.

    Present-but-wrong is refused on the same argument as a bad ``src``: layout
    is set by a control, whole, never half-typed, so there is no mid-edit state
    to be caught in. What it keeps out is a ``width`` of 1e9 reaching a reader.
    """
    align = attrs.get("align")
    if align is not None and align not in MEDIA_ALIGNMENTS:
        raise UnsafeMediaError(
            f"a media block in this draft is aligned {_describe(align)}, which is "
            f"not one of {', '.join(sorted(MEDIA_ALIGNMENTS))}. Re-set the wrap "
            "with the controls on the block."
        )

    width = attrs.get("width")
    if width is None:
        return
    # bool is an int in Python, and `width: true` is not a width.
    if isinstance(width, bool) or not isinstance(width, int | float):
        raise UnsafeMediaError(
            f"a media block in this draft has a width of {_describe(width)}, which "
            "is not a number. Re-set the size with the controls on the block."
        )
    if not MEDIA_MIN_WIDTH <= width <= MEDIA_MAX_WIDTH:
        raise UnsafeMediaError(
            f"a media block in this draft is {width}% wide, outside the "
            f"{MEDIA_MIN_WIDTH}-{MEDIA_MAX_WIDTH}% a column can show. Re-set the "
            "size with the controls on the block."
        )


def _describe(value: object) -> str:
    """A short, quoted rendering of a rejected value for the error message."""
    if isinstance(value, str):
        return repr(value if len(value) <= 80 else value[:77] + "…")
    if value is None:
        return "nothing"
    return repr(value)
