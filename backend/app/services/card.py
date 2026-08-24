"""Feed cards are derived from the article. They are never generated.

There is no card-generation prompt and no card field the model controls
independently. One AI output, two renderings — so the card and the article
cannot contradict each other. A separate generation call is the obvious
alternative and it is exactly the thing that produces a confident card sitting
above a hedged article.

Derivation runs at publish time, from ``COALESCE(edited_content,
original_content)``, so the card reflects what the human approved rather than
what the model first wrote. The result is materialised onto the article row to
keep the feed query a single index scan.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.domain.enums import Verdict
from app.services.tiptap import beat_text, iter_nodes

CARD_EXCERPT_MAX_CHARS = 180

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class DerivedCard:
    headline: str
    excerpt: str
    verdict: Verdict
    #: The article's own first picture, or None. See ``_lead_image``.
    image: str | None = None
    image_alt: str | None = None


def derive_card(headline: str, body_doc: dict, verdict: Verdict) -> DerivedCard:
    """Compute the card from the approved article content.

    The excerpt is the first sentence of beat 1 with citation markers stripped
    — ``[S1]`` in a feed card is noise to a reader with no source list in front
    of them.
    """
    first_beat = beat_text(body_doc, beat=1, keep_citations=False)
    excerpt = _first_sentence(first_beat)
    image, image_alt = _lead_image(body_doc)
    return DerivedCard(
        headline=headline,
        excerpt=_truncate(excerpt, CARD_EXCERPT_MAX_CHARS),
        verdict=verdict,
        image=image,
        image_alt=image_alt,
    )


def _lead_image(doc: dict) -> tuple[str | None, str | None]:
    """The first image node's ``src``, or ``(None, None)``.

    Same rule as every other field here: the card shows what the article shows,
    so the tile is the article's own first picture rather than an image chosen
    for the feed. A reviewer who wants a different tile changes the article,
    which is the only edit that cannot make the two disagree.

    Only ``image`` nodes count — a ``youtube`` block would mean fetching a
    thumbnail from a third party on every feed render, which is exactly the
    tracking the article page's click-to-load facade exists to avoid.

    ``None`` is the normal case and not a fallback to fill in: the feed draws a
    typographic tile, which is the design's resting state.
    """
    for node in iter_nodes(doc):
        if node.get("type") != "image":
            continue
        attrs: Any = node.get("attrs")
        if not isinstance(attrs, dict):
            continue
        src = attrs.get("src")
        if not isinstance(src, str) or not src:
            continue
        alt = attrs.get("alt")
        return src, alt if isinstance(alt, str) and alt else None
    return None, None


def _first_sentence(text: str) -> str:
    cleaned = " ".join(text.split())
    if not cleaned:
        return ""
    parts = _SENTENCE_END_RE.split(cleaned, maxsplit=1)
    return parts[0].strip()


def _truncate(text: str, limit: int) -> str:
    """Truncate on a word boundary, with an ellipsis.

    Mid-word truncation in a card looks like a rendering bug, which undermines
    exactly the credibility this product trades on.
    """
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1]
    if " " in clipped:
        clipped = clipped[: clipped.rfind(" ")]
    return clipped.rstrip(",;: ") + "…"
