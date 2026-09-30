"""Serving stored media. Read-only, unauthenticated, public.

Unauthenticated by design: these images are embedded in published articles, so
every reader of every article fetches them. Uploading is the console-only half
and lives on the authenticated router.

This replaces a ``StaticFiles`` mount. Two things came with the move that are
worth knowing, because neither is incidental:

* **The content type is now correct.** ``StaticFiles`` guesses from the
  filename via ``mimetypes``, and on a stock Python/macOS install ``.webp`` is
  not in the table — every generated illustration was being served as
  ``application/octet-stream``. Browsers sniff images in ``<img>`` so it
  rendered anyway, which is exactly why nobody noticed. Here the type comes
  from ``CONTENT_TYPES``, keyed by the extension the sniffer decided when the
  bytes were stored.
* **Path traversal stops being a category of bug rather than a handled one.**
  There is no path. The two URL segments are matched against a strict regex and
  become a 64-character lookup key; ``..`` does not parse, and a request that
  does not match the shape never reaches the database.
"""

from __future__ import annotations

import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Response, status

from app.api.deps import SessionDep
from app.services.media import CONTENT_TYPES, load_image

router = APIRouter(tags=["public"])

#: The two path segments. Together they are the digest, so they are constrained
#: exactly as ``MEDIA_SRC_RE`` constrains them inside a document — a URL this
#: route accepts and a src that check refuses would be two definitions of what
#: our own media looks like.
#:
#: Matched in the handler rather than declared as a FastAPI ``pattern``, which
#: would answer 422 with a validation body. Every way of naming media we do not
#: have should look identical from outside: a 422 on a malformed shard and a
#: 404 on an absent one tells a prober which half of the path was wrong.
_SHARD_RE = re.compile(r"^[0-9a-f]{2}$")
_NAME_RE = re.compile(r"^([0-9a-f]{62})\.(png|jpg|gif|webp)$")

#: A year, immutable. Content-addressed bytes cannot change under a URL: a
#: different image is a different digest and therefore a different path. This
#: is the one caching header that is simply true rather than a guess.
_CACHE_CONTROL = "public, max-age=31536000, immutable"


@router.get(
    "/media/{shard}/{name}",
    response_class=Response,
    responses={
        200: {"content": {"image/webp": {}}, "description": "The stored image"},
        404: {"description": "No such media"},
    },
)
async def get_media(
    session: SessionDep,
    shard: Annotated[str, Path(description="First 2 hex of the digest")],
    name: Annotated[str, Path(description="Remaining 62 hex, plus the extension")],
) -> Response:
    """Serve one stored image by its content address.

    404 covers every miss — a malformed path, an unknown digest, and a digest
    stored under a different format — deliberately: the distinctions are only
    interesting to someone probing the store, and there is nothing a legitimate
    client would do differently for any of them.
    """
    matched = _NAME_RE.match(name)
    if matched is None or _SHARD_RE.match(shard) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such media.")
    rest, extension = matched.groups()

    row = await load_image(shard + rest, extension, session=session)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such media.")

    return Response(
        content=row.data,
        media_type=CONTENT_TYPES[row.extension],
        headers={"Cache-Control": _CACHE_CONTROL},
    )
