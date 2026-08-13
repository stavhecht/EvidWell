"""Content-shaping tests: field bounds, TipTap conversion, card derivation.

The card tests are the ones that matter most here. "The feed card is derived
from the article, not generated separately" is a structural guarantee, and
these are what hold it.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.contracts import (
    ArticleBody,
    CitationGroup,
    ExtractionOutput,
    SynthesisOutput,
    count_sentences,
    extract_handles,
)
from app.domain.enums import Verdict
from app.services.card import CARD_EXCERPT_MAX_CHARS, derive_card
from app.services.tiptap import (
    MalformedBodyError,
    beat_text,
    body_text_to_doc,
    cited_handles_in_doc,
    doc_to_plain_text,
)

# ---------------------------------------------------------------------------
# Field bounds (DESIGN.md §6). Ceilings, enforced per field rather than as a
# global word count, so thin evidence yields a short honest article.
# ---------------------------------------------------------------------------


def test_headline_over_twelve_words_is_rejected() -> None:
    with pytest.raises(ValidationError, match="12 words"):
        SynthesisOutput(
            headline=" ".join(["word"] * 13),
            verdict=Verdict.WEAK,
            summary="Short.",
            body=ArticleBody(
                beat_1_claim="Claims things.",
                beat_2_evidence="Evidence [S1].",
                beat_3_bottom_line="Bottom line.",
            ),
        )


def test_beat_over_three_sentences_is_rejected() -> None:
    with pytest.raises(ValidationError, match="3 sentences"):
        ArticleBody(
            beat_1_claim="One. Two. Three. Four.",
            beat_2_evidence="Evidence [S1].",
            beat_3_bottom_line="Bottom line.",
        )


def test_summary_over_two_sentences_is_rejected() -> None:
    with pytest.raises(ValidationError, match="2 sentences"):
        SynthesisOutput(
            headline="Short headline",
            verdict=Verdict.WEAK,
            summary="One. Two. Three.",
            body=ArticleBody(
                beat_1_claim="Claims.",
                beat_2_evidence="Evidence [S1].",
                beat_3_bottom_line="Bottom.",
            ),
        )


def test_a_one_sentence_article_is_valid() -> None:
    """Length is a ceiling, not a floor.

    Thin evidence must be allowed to produce a short article; nothing in the
    system may require padding to reach a length.
    """
    output = SynthesisOutput(
        headline="Not enough evidence yet",
        verdict=Verdict.WEAK,
        summary="One small trial is not enough to conclude anything.",
        body=ArticleBody(
            beat_1_claim="It claims to improve sleep.",
            beat_2_evidence="One small trial of 40 people found a modest effect [S1].",
            beat_3_bottom_line="That is not enough to conclude anything.",
        ),
    )
    assert output.verdict is Verdict.WEAK


def test_verdict_qualifier_must_be_one_short_clause() -> None:
    with pytest.raises(ValidationError, match="single short clause"):
        SynthesisOutput(
            headline="Headline",
            verdict=Verdict.MIXED,
            verdict_qualifier=" ".join(["word"] * 20),
            summary="Short.",
            body=ArticleBody(
                beat_1_claim="Claims.",
                beat_2_evidence="Evidence [S1].",
                beat_3_bottom_line="Bottom.",
            ),
        )


def test_sentence_counting_tolerates_trailing_whitespace() -> None:
    assert count_sentences("One. Two.  ") == 2
    assert count_sentences("No terminator") == 1


def test_handle_extraction_finds_multi_digit_handles() -> None:
    assert extract_handles("a [S1] b [S12] c") == {"S1", "S12"}


# ---------------------------------------------------------------------------
# Citation handle shape.
#
# A run was lost to `source_ids: ["S1-S8"]` — the shorthand a model reaches for
# when every source backs the same claim. Two independent defences: the schema
# pattern stops it being generated, the before-validator repairs it if some
# other provider emits it anyway.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("shorthand", "expected"),
    [
        ("S1-S8", [f"S{n}" for n in range(1, 9)]),
        ("S1-3", ["S1", "S2", "S3"]),
        ("S1\u2013S3", ["S1", "S2", "S3"]),  # en dash
        ("S2 - S4", ["S2", "S3", "S4"]),
        ("S5-S5", ["S5"]),
    ],
)
def test_handle_ranges_expand(shorthand: str, expected: list[str]) -> None:
    assert CitationGroup(claim="c", source_ids=[shorthand]).source_ids == expected


def test_comma_packed_handles_split() -> None:
    group = CitationGroup(claim="c", source_ids=["S1, S2", "S4"])
    assert group.source_ids == ["S1", "S2", "S4"]


def test_overlapping_shorthand_dedupes_in_order() -> None:
    group = CitationGroup(claim="c", source_ids=["S1-S3", "S2"])
    assert group.source_ids == ["S1", "S2", "S3"]


def test_absurd_range_is_rejected_not_expanded() -> None:
    # Inventing 900 handles we never retrieved is worse than failing loudly.
    with pytest.raises(ValidationError):
        CitationGroup(claim="c", source_ids=["S1-S900"])


@pytest.mark.parametrize("handle", ["SX", "1", "S", "S1x", "", "source 1"])
def test_genuinely_malformed_handles_still_rejected(handle: str) -> None:
    with pytest.raises(ValidationError):
        CitationGroup(claim="c", source_ids=[handle])


def test_handle_pattern_reaches_the_structured_output_schema() -> None:
    """The constraint is only worth anything if the grammar can see it."""
    schema = SynthesisOutput.model_json_schema()
    items = schema["$defs"]["CitationGroup"]["properties"]["source_ids"]["items"]
    assert items["pattern"] == "^S[0-9]+$"


def test_schema_patterns_avoid_escapes_ollama_cannot_compile() -> None:
    """Guard the `[0-9]`-not-`\\d` decision in contracts.py.

    Ollama compiles `pattern` into GBNF and its converter rejects the `\\d`
    class escapes outright — the request fails at 400 "failed to parse grammar",
    which takes down *every* call using the schema, not just a malformed one.
    Tidying `[0-9]` back to `\\d` therefore looks harmless and is not.
    """
    unsupported = ("\\d", "\\w", "\\s", "\\D", "\\W", "\\S")

    def patterns(node: object) -> list[str]:
        if isinstance(node, dict):
            found = [node["pattern"]] if isinstance(node.get("pattern"), str) else []
            return found + [p for v in node.values() for p in patterns(v)]
        if isinstance(node, list):
            return [p for item in node for p in patterns(item)]
        return []

    for model in (SynthesisOutput, ExtractionOutput):
        for pattern in patterns(model.model_json_schema()):
            assert not any(esc in pattern for esc in unsupported), (
                f"{model.__name__}: pattern {pattern!r} uses an escape Ollama "
                f"cannot compile; use an explicit class like [0-9]"
            )


# ---------------------------------------------------------------------------
# TipTap conversion.
# ---------------------------------------------------------------------------


def _body(beat_2: str = "Cortisol fell [S1].") -> ArticleBody:
    return ArticleBody(
        beat_1_claim="It claims to reduce stress.",
        beat_2_evidence=beat_2,
        beat_3_bottom_line="Early evidence only.",
    )


def test_citations_become_first_class_nodes() -> None:
    doc = body_text_to_doc(_body())
    paragraph = doc["content"][1]
    types = [child["type"] for child in paragraph["content"]]
    assert "citation" in types
    citation = next(c for c in paragraph["content"] if c["type"] == "citation")
    assert citation["attrs"]["sourceIds"] == ["S1"]


def test_adjacent_markers_collapse_into_one_node() -> None:
    """`[S1][S3]` is one chip, not two.

    A row of separate chips reads as several findings when it is one.
    """
    doc = body_text_to_doc(_body("Cortisol fell [S1][S3] in both trials."))
    paragraph = doc["content"][1]
    citations = [c for c in paragraph["content"] if c["type"] == "citation"]
    assert len(citations) == 1
    assert citations[0]["attrs"]["sourceIds"] == ["S1", "S3"]


def test_beats_are_tagged_by_number_not_position() -> None:
    doc = body_text_to_doc(_body())
    assert [node["attrs"]["beat"] for node in doc["content"]] == [1, 2, 3]


def test_malformed_marker_raises() -> None:
    """A body we cannot render is a draft problem, surfaced as a failure code."""
    with pytest.raises(MalformedBodyError):
        body_text_to_doc(_body("Cortisol fell [S1 and rose [source 2]."))


def test_round_trip_preserves_citations() -> None:
    body = _body("Cortisol fell [S1] and sleep improved [S3].")
    doc = body_text_to_doc(body)
    restored = doc_to_plain_text(doc, keep_citations=True)
    assert "[S1]" in restored
    assert "[S3]" in restored


def test_stripping_citations_leaves_clean_prose() -> None:
    doc = body_text_to_doc(_body("Cortisol fell [S1]."))
    assert "[S1]" not in doc_to_plain_text(doc, keep_citations=False)


def test_cited_handles_in_doc_finds_every_handle() -> None:
    """Used at approve time to re-validate human-edited content."""
    doc = body_text_to_doc(_body("A [S1] and B [S2][S3]."))
    assert cited_handles_in_doc(doc) == {"S1", "S2", "S3"}


def test_beat_text_addresses_by_tag_not_index() -> None:
    doc = body_text_to_doc(_body())
    # Simulate a reviewer inserting a paragraph ahead of the beats.
    doc["content"].insert(0, {"type": "paragraph", "attrs": {}, "content": []})
    assert beat_text(doc, beat=1).startswith("It claims")


# ---------------------------------------------------------------------------
# Card derivation. One AI output, two renderings.
# ---------------------------------------------------------------------------


def test_card_excerpt_is_first_sentence_of_beat_one_without_markers() -> None:
    doc = body_text_to_doc(
        ArticleBody(
            beat_1_claim="It claims to reduce stress [S1]. It also claims better sleep.",
            beat_2_evidence="A trial found lower cortisol [S1].",
            beat_3_bottom_line="Early days.",
        )
    )
    card = derive_card("Ashwagandha and stress", doc, Verdict.MIXED)

    assert card.excerpt == "It claims to reduce stress."
    assert "[S1]" not in card.excerpt


def test_card_verdict_always_matches_the_article_verdict() -> None:
    """The property a separate card-generation call would not give us."""
    doc = body_text_to_doc(_body())
    for verdict in Verdict:
        assert derive_card("Headline", doc, verdict).verdict is verdict


def test_long_excerpt_truncates_on_a_word_boundary() -> None:
    """Mid-word truncation reads as a rendering bug, which costs credibility."""
    long_claim = "It claims " + "to do many different wellness things " * 10
    doc = body_text_to_doc(
        ArticleBody(
            beat_1_claim=long_claim.strip() + ".",
            beat_2_evidence="Evidence [S1].",
            beat_3_bottom_line="Bottom.",
        )
    )
    card = derive_card("Headline", doc, Verdict.WEAK)

    assert len(card.excerpt) <= CARD_EXCERPT_MAX_CHARS
    assert card.excerpt.endswith("…")
    assert not card.excerpt[:-1].endswith(" ")


def test_card_survives_an_empty_body() -> None:
    """A malformed draft still persists (as validation_failed); deriving a card
    from its empty document must not raise."""
    card = derive_card("Headline", {"type": "doc", "content": []}, Verdict.NO_EVIDENCE)
    assert card.excerpt == ""
