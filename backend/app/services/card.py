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

from app.domain.enums import Verdict
from app.services.tiptap import beat_text

CARD_EXCERPT_MAX_CHARS = 180

_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class DerivedCard:
    headline: str
    excerpt: str
    verdict: Verdict


def derive_card(headline: str, body_doc: dict, verdict: Verdict) -> DerivedCard:
    """Compute the card from the approved article content.

    The excerpt is the first sentence of beat 1 with citation markers stripped
    — ``[S1]`` in a feed card is noise to a reader with no source list in front
    of them.
    """
    first_beat = beat_text(body_doc, beat=1, keep_citations=False)
    excerpt = _first_sentence(first_beat)
    return DerivedCard(
        headline=headline,
        excerpt=_truncate(excerpt, CARD_EXCERPT_MAX_CHARS),
        verdict=verdict,
    )


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
