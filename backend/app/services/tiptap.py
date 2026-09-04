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
      ]},
      {"type": "heading", "attrs": {"level": 2}, "content": [
        {"type": "text", "text": "What the trials measured"}
      ]},
      {"type": "paragraph", "content": [...]}
    ]}

Three block types come out of this module: ``paragraph``, ``heading`` (a
section title, always level 2), and — passed in rather than parsed — ``image``.
A reviewer can add ``image`` and ``youtube`` in the console. Beat paragraphs
carry ``attrs.beat``; section paragraphs and reviewer-added ones do not.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from app.domain.contracts import CITATION_MARKER_PATTERN, ArticleBody

#: Splits on a run of adjacent markers, so ``[S1][S3]`` and ``[S1, S3]`` both
#: become one citation node. A row of separate chips reads as three findings
#: when it is one.
#:
#: Built from the contracts pattern rather than restating it: the renderer and
#: ``extract_handles`` disagreeing about what a marker is means a source the
#: article visibly cites is recorded as uncited.
_CITATION_RUN_RE = re.compile(rf"((?:{CITATION_MARKER_PATTERN})+)")
_HANDLE_RE = re.compile(r"S\d+")

#: Any bracket still standing once every well-formed marker is removed.
#:
#: Checked by elimination rather than with a "not a valid marker" pattern. The
#: negative form has to enumerate every way a marker can be wrong, and it missed
#: unterminated runs like ``"[S1, S5"`` — which matched nothing, fell through as
#: literal text, and printed a broken marker into the finished article instead
#: of failing. Anything bracketed that is not a marker is a parse failure.
_STRAY_BRACKET_RE = re.compile(r"\[[^\]]*\]?|\]")


class MalformedBodyError(ValueError):
    """The model's body text could not be parsed into a document.

    Surfaces as a ``malformed_body`` validation failure, not an exception —
    the model produced something unrenderable, which is a draft problem rather
    than a system problem.
    """


def body_text_to_doc(
    body: ArticleBody, *, lead_image: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Parse the three beats into a TipTap document.

    Beats are tagged ``attrs.beat`` 1–3 so the editor and the card deriver can
    address them without positional guessing.

    ``lead_image`` — built by ``services/media.py::image_node`` — is prepended
    as a sibling of the beat paragraphs, which is the same place a reviewer's
    own image block sits (DESIGN.md §3.4b). Prepending is safe precisely
    *because* beats are addressed by ``attrs.beat``: ``beat_text`` still finds
    beat 1 for the card excerpt, and the walks below skip a node with no
    ``content`` without producing an empty paragraph.

    Sections (optional, and often absent) sit between beats 2 and 3 as an ``h2``
    followed by one paragraph per blank-line-separated block. **Their paragraphs
    carry no ``beat`` attribute**, for the same reason a reviewer's own added
    paragraph carries none: a section is a sibling of the beats, not a fourth
    beat, and must not answer to ``beat_text``.

    Raises:
        MalformedBodyError: unbalanced brackets, or a marker that isn't
            ``S<digits>``.
    """
    blocks: list[dict[str, Any]] = [
        _paragraph(body.beat_1_claim, beat=1, where="beat 1"),
        _paragraph(body.beat_2_evidence, beat=2, where="beat 2"),
    ]

    for index, section in enumerate(body.sections, start=1):
        where = f"section {index}"
        blocks.append(_heading(section.heading, where=f"{where} heading"))
        # A section may be several paragraphs. Splitting here rather than
        # asking the model for a list keeps the generated shape one string per
        # section, which is what the structured-output grammar handles well.
        for block in _split_paragraphs(section.body):
            blocks.append(_paragraph(block, beat=None, where=where))

    blocks.append(_paragraph(body.beat_3_bottom_line, beat=3, where="beat 3"))

    if lead_image is not None:
        blocks.insert(0, lead_image)
    return {"type": "doc", "content": blocks}


#: A blank line, however much trailing whitespace the model leaves on it.
_PARAGRAPH_BREAK_RE = re.compile(r"\n[ \t]*\n+")


def _split_paragraphs(text: str) -> list[str]:
    """A section's prose as one string per paragraph.

    Never returns an empty list: ``ArticleSection.body`` is ``NonEmptyStr``, so
    a section that happens to hold no blank line is simply one paragraph.
    """
    return [block.strip() for block in _PARAGRAPH_BREAK_RE.split(text) if block.strip()]


def _inline_content(text: str, *, where: str) -> list[dict[str, Any]]:
    """Text and citation nodes, in order.

    ``where`` names the block for the error message only — a reviewer reading a
    ``malformed_body`` failure needs to know which block to look at.
    """
    if match := _STRAY_BRACKET_RE.search(_CITATION_RUN_RE.sub("", text)):
        raise MalformedBodyError(
            f"{where} contains a malformed citation marker: {match.group(0)!r}"
        )

    content: list[dict[str, Any]] = []
    for segment in _CITATION_RUN_RE.split(text):
        if not segment:
            continue
        # ``fullmatch`` rather than "did we find handles here": the handle
        # pattern is unanchored, so testing it against an arbitrary segment
        # would read prose like "the S1 group" as a citation.
        if _CITATION_RUN_RE.fullmatch(segment):
            # Preserve order, drop duplicates within the run.
            handles = list(dict.fromkeys(_HANDLE_RE.findall(segment)))
            content.append({"type": "citation", "attrs": {"sourceIds": handles}})
        else:
            content.append({"type": "text", "text": segment})

    return content


def _paragraph(text: str, *, beat: int | None, where: str) -> dict[str, Any]:
    node: dict[str, Any] = {
        "type": "paragraph",
        "content": _inline_content(text, where=where),
    }
    if beat is not None:
        node["attrs"] = {"beat": beat}
    return node


def _heading(text: str, *, where: str) -> dict[str, Any]:
    """A section title, as an ``h2``.

    Runs through the same marker check as prose. A heading should not carry a
    citation, but an unbalanced bracket in one would print into the finished
    article exactly as it would in a paragraph, so it is checked rather than
    trusted.
    """
    return {
        "type": "heading",
        "attrs": {"level": 2},
        "content": _inline_content(text, where=where),
    }


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


def iter_nodes(node: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every node in a document, depth first, the root included.

    The walks above stop two levels down because the beats are flat paragraphs
    and that is genuinely all they contain. This one recurses because it backs
    a security check (``services/media.py``), and a check that only looks where
    content is *supposed* to be is not a check. Non-dict children are skipped
    rather than raising: the caller is auditing a document that arrived over
    HTTP, so malformed is an expected input, not an exception.
    """
    yield node
    children = node.get("content")
    if isinstance(children, list):
        for child in children:
            if isinstance(child, dict):
                yield from iter_nodes(child)


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
