"""Conversion between the model's marker syntax and TipTap documents.

The model emits plain text with ``[S1]`` markers. We store TipTap JSON with
citations as first-class inline nodes. This module is the boundary.

Why not just ask the model for TipTap JSON: it costs tokens to describe the
schema, invites malformed output, and puts document structure inside the thing
we are trying to constrain. Marker syntax is trivial for the model and trivial
to parse. Why not just store markdown: citations stay strings, so the editor
can't render a clickable chip, validation has to regex prose, and diffing
original against edited is textual rather than structural.

Document shape::

    {"type": "doc", "content": [
      {"type": "paragraph", "attrs": {"beat": 1}, "content": [
        {"type": "text", "text": "One trial found lower cortisol "},
        {"type": "citation", "attrs": {"sourceIds": ["S1", "S3"]}}
      ]}
    ]}
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.contracts import ArticleBody

#: Splits on a run of adjacent markers so ``[S1][S3]`` becomes one citation
#: node. A row of separate chips reads as three findings when it is one.
_CITATION_RUN_RE = re.compile(r"((?:\[S\d+\])+)")
_SINGLE_HANDLE_RE = re.compile(r"\[(S\d+)\]")

#: Any bracketed token that is *not* a well-formed handle. Catches "[S]",
#: "[source 1]", and half-deleted markers like "[S1".
_MALFORMED_RE = re.compile(r"\[(?!S\d+\])[^\]]*\]|\[S\d+(?!\])")


class MalformedBodyError(ValueError):
    """The model's body text could not be parsed into a document.

    Surfaces as a ``malformed_body`` validation failure, not an exception —
    the model produced something unrenderable, which is a draft problem rather
    than a system problem.
    """


def body_text_to_doc(body: ArticleBody) -> dict[str, Any]:
    """Parse the three beats into a TipTap document.

    Beats are tagged ``attrs.beat`` 1–3 so the editor and the card deriver can
    address them without positional guessing.

    Raises:
        MalformedBodyError: unbalanced brackets, or a marker that isn't
            ``S<digits>``.
    """
    beats = [
        body.beat_1_claim,
        body.beat_2_evidence,
        body.beat_3_bottom_line,
    ]
    return {
        "type": "doc",
        "content": [
            _paragraph(text, beat_number)
            for beat_number, text in enumerate(beats, start=1)
        ],
    }


def _paragraph(text: str, beat: int) -> dict[str, Any]:
    if match := _MALFORMED_RE.search(text):
        raise MalformedBodyError(
            f"beat {beat} contains a malformed citation marker: {match.group(0)!r}"
        )

    content: list[dict[str, Any]] = []
    for segment in _CITATION_RUN_RE.split(text):
        if not segment:
            continue
        handles = _SINGLE_HANDLE_RE.findall(segment)
        if handles:
            # Preserve order, drop duplicates within the run.
            content.append(
                {
                    "type": "citation",
                    "attrs": {"sourceIds": list(dict.fromkeys(handles))},
                }
            )
        else:
            content.append({"type": "text", "text": segment})

    return {"type": "paragraph", "attrs": {"beat": beat}, "content": content}


#: Whitespace stranded before punctuation once a citation node is removed.
#: "cortisol fell [S1]." -> "cortisol fell ." without this.
_ORPHANED_SPACE_RE = re.compile(r"\s+([.,;:!?])")


def doc_to_plain_text(doc: dict[str, Any], *, keep_citations: bool = True) -> str:
    """Flatten a document back to text.

    ``keep_citations=True`` restores ``[S1]`` markers — used when re-validating
    human-edited content. ``False`` strips them, for card excerpts and search,
    and tidies the whitespace their removal leaves behind.
    """
    paragraphs: list[str] = []
    for node in doc.get("content", []):
        parts: list[str] = []
        for child in node.get("content", []):
            if child.get("type") == "text":
                parts.append(child.get("text", ""))
            elif child.get("type") == "citation" and keep_citations:
                handles = child.get("attrs", {}).get("sourceIds", [])
                parts.append("".join(f"[{handle}]" for handle in handles))

        text = "".join(parts)
        if not keep_citations:
            text = _ORPHANED_SPACE_RE.sub(r"\1", text)
        paragraphs.append(text.strip())

    return "\n\n".join(part for part in paragraphs if part)


def cited_handles_in_doc(doc: dict[str, Any]) -> set[str]:
    """Every handle referenced by a citation node.

    Used to re-validate ``edited_content`` at approve time: a reviewer can
    delete a sentence and orphan a citation, or paste in a handle that was
    never provided. Invariant #2 has to survive human editing, not just
    generation.
    """
    handles: set[str] = set()
    for node in doc.get("content", []):
        for child in node.get("content", []):
            if child.get("type") == "citation":
                handles.update(child.get("attrs", {}).get("sourceIds", []))
    return handles


def beat_text(doc: dict[str, Any], beat: int, *, keep_citations: bool = False) -> str:
    """One beat's text, addressed by its ``attrs.beat`` tag rather than index.

    Position would break the moment a reviewer adds a paragraph, and the card
    excerpt is derived from beat 1 specifically.
    """
    for node in doc.get("content", []):
        if node.get("attrs", {}).get("beat") == beat:
            single = {"type": "doc", "content": [node]}
            return doc_to_plain_text(single, keep_citations=keep_citations)
    return ""
