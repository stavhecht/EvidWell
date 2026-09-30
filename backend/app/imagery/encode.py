"""Encoding and cropping for generated images, shared by every image provider."""

from __future__ import annotations

import io
from typing import Any

from app.imagery.base import ImageError, RenderedImage

#: WebP, at a quality that is visually indistinguishable from the PNG the
#: provider returns and roughly a tenth the size. It matters twice: these files
#: are served from our own origin on every feed render, and ``store_image``
#: caps a file at ``media_max_bytes``. ``method=6`` is the slowest, smallest
#: encoder setting — worth it here because encoding happens once and the result
#: is content-addressed forever.
WEBP_QUALITY = 82
WEBP_METHOD = 6
WEBP_CONTENT_TYPE = "image/webp"


def encode_webp(image: Any) -> tuple[bytes, int, int]:
    """PIL image -> (webp bytes, width, height). Runs off the event loop.

    Dimensions are read off the decoded image rather than echoed from the
    request, so a provider that rounded to its own grid is caught rather than
    believed.
    """
    from PIL import Image

    if not isinstance(image, Image.Image):
        raise ImageError(f"expected an image from the provider, got {type(image).__name__}")

    # WebP has no alpha problem, but a P- or LA-mode image encodes poorly and a
    # CMYK one not at all. RGB is what every one of these renders is anyway.
    prepared = image if image.mode == "RGB" else image.convert("RGB")

    buffer = io.BytesIO()
    try:
        prepared.save(buffer, format="WEBP", quality=WEBP_QUALITY, method=WEBP_METHOD)
    except Exception as exc:
        raise ImageError(f"could not encode the render as WebP: {exc}") from exc
    return buffer.getvalue(), prepared.width, prepared.height


def crop_to_ratio(image: RenderedImage, ratio: tuple[int, int]) -> RenderedImage:
    """Centre-crop a rendered image to ``ratio`` (width, height), as WebP.

    The cover mode ``crop`` uses this to cut the portrait feed tile out of the
    landscape lead: one render instead of two, and the two frames are then
    provably one photograph. Safe because the still-life compositions centre
    their subject in negative space (see ``imagery/prompt.py``). Runs off the
    event loop — call it through ``asyncio.to_thread``.
    """
    from PIL import Image

    target = ratio[0] / ratio[1]
    try:
        with Image.open(io.BytesIO(image.data)) as source:
            source.load()
            width, height = source.size
            if width / height > target:
                new_width = round(height * target)
                left = (width - new_width) // 2
                box = (left, 0, left + new_width, height)
            else:
                new_height = round(width / target)
                top = (height - new_height) // 2
                box = (0, top, width, top + new_height)
            cropped = source.crop(box)
    except ImageError:
        raise
    except Exception as exc:
        raise ImageError(f"could not crop the render: {exc}") from exc
    data, cropped_width, cropped_height = encode_webp(cropped)
    return RenderedImage(
        data=data,
        content_type=WEBP_CONTENT_TYPE,
        width=cropped_width,
        height=cropped_height,
        seed=image.seed,
    )
