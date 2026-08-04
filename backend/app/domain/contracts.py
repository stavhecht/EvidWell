"""The typed contracts every stage of the pipeline speaks.

This module is the spec. If a stage's input or output isn't described here, it
isn't defined. The two generative LLM calls use ``ExtractionOutput`` and
``SynthesisOutput`` directly as structured-output schemas, so these models are
simultaneously our internal types and the constraint the model generates under.

Field bounds (DESIGN.md §6) are enforced here as validators rather than as a
global word count, so that thin evidence naturally produces a short, honest
article instead of padding to reach a floor.
"""

from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from app.domain.enums import SourceApi, StudyType, Verdict

# Matches a citation handle as the model emits it inline: [S1], [S2]…
CITATION_MARKER_RE = re.compile(r"\[(S\d+)\]")

# A rough sentence splitter. Deliberately simple: it is used to enforce a
# *ceiling*, so over-counting on an edge case (an abbreviation like "e.g.")
# fails safe by rejecting a too-long draft rather than admitting one.
_SENTENCE_RE = re.compile(r"[.!?](?:\s|$)")


def count_sentences(text: str) -> int:
    return len([s for s in _SENTENCE_RE.split(text.strip()) if s.strip()])


def extract_handles(text: str) -> set[str]:
    """Every citation handle appearing inline in a body of text."""
    return set(CITATION_MARKER_RE.findall(text))


NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


# ---------------------------------------------------------------------------
# LLM call 1 — claim + ingredient extraction
# ---------------------------------------------------------------------------


class ExtractionInput(BaseModel):
    """What we know before any retrieval: a topic, and maybe marketing copy."""

    topic: NonEmptyStr = Field(description="Product or trend, e.g. 'ashwagandha for stress'")
    blurb: str | None = Field(default=None, description="Optional marketing copy")


class ExtractionOutput(BaseModel):
    """Structured-output schema for LLM call 1.

    The cap on ``target_claims`` is a cost control as much as a quality one:
    each claim fans out into its own retrieval pass across four providers.
    """

    product: NonEmptyStr
    target_claims: list[NonEmptyStr] = Field(min_length=1, max_length=6)
    ingredients: list[NonEmptyStr] = Field(default_factory=list, max_length=12)


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


class CandidatePaper(BaseModel):
    """A paper as returned by a scholarly provider, normalised.

    Every provider adapter maps its own response shape into this. At least one
    of ``pmid``/``doi`` must be present — a paper we cannot resolve to a real
    identifier can never satisfy the grounding rule, so it is dropped at the
    adapter boundary rather than carried forward.
    """

    pmid: str | None = None
    doi: str | None = None
    title: NonEmptyStr
    abstract: NonEmptyStr
    journal: str | None = None
    year: int | None = None
    study_type: StudyType = StudyType.UNKNOWN
    raw_study_type: str | None = Field(
        default=None,
        description="Provider's own publication-type string, kept so the "
        "classifier can be re-run over the cache without re-fetching.",
    )
    citation_count: int | None = None
    url: str | None = None
    source_api: SourceApi

    @property
    def dedup_key(self) -> str:
        """Cross-provider identity. The first of :attr:`identity_keys`.

        DOI, then PMID, then a normalised title. The same paper routinely
        arrives from three providers with three different identifier subsets,
        so the fallback chain matters more than it looks.
        """
        return self.identity_keys[0]

    @property
    def identity_keys(self) -> list[str]:
        """*Every* key this paper could be recognised by, strongest first.

        A single key is not enough to match across providers. PubMed can return
        a record with a PMID and no DOI while OpenAlex returns the same paper
        with both — one would key on ``pmid:``, the other on ``doi:``, and
        matching on ``dedup_key`` alone would treat them as two studies. Two
        handles for one paper is the failure that makes thin evidence look
        corroborated, so the merge matches on any shared key.
        """
        keys = []
        if self.doi:
            keys.append(f"doi:{self.doi.strip().lower()}")
        if self.pmid:
            keys.append(f"pmid:{self.pmid.strip()}")
        keys.append("title:" + re.sub(r"[^a-z0-9]+", "", self.title.lower()))
        return keys


class RankedSource(BaseModel):
    """A cached source with its score against one specific claim."""

    source_id: str
    claim: str
    citation_handle: str = Field(pattern=r"^S\d+$")
    paper: CandidatePaper
    cosine_similarity: float
    final_score: float = Field(
        description="cosine + grade bonus + recency bonus; see retrieval/rerank.py"
    )


# ---------------------------------------------------------------------------
# LLM call 2 — synthesis (RAG generation)
# ---------------------------------------------------------------------------


class PromptSource(BaseModel):
    """One source exactly as it is rendered into the synthesis prompt.

    The handle set built from these is the *only* set of ids the model may
    emit. Validation compares against this, not against the database.
    """

    handle: str = Field(pattern=r"^S\d+$")
    title: str
    abstract: str
    journal: str | None
    year: int | None
    study_type: StudyType
    source_id: str


class SynthesisInput(BaseModel):
    product: str
    target_claims: list[str]
    sources: list[PromptSource] = Field(min_length=1)

    @property
    def handle_set(self) -> set[str]:
        return {s.handle for s in self.sources}


class ArticleBody(BaseModel):
    """The three beats. Each is a ceiling, not a target.

    A beat that would be honest at one sentence must stay one sentence — see
    DESIGN.md §6. Nothing in this system pads to length.
    """

    beat_1_claim: NonEmptyStr = Field(description="What it claims to do")
    beat_2_evidence: NonEmptyStr = Field(description="What the research actually shows")
    beat_3_bottom_line: NonEmptyStr = Field(description="Bottom line / caveat")

    @field_validator("beat_1_claim", "beat_2_evidence", "beat_3_bottom_line")
    @classmethod
    def _at_most_three_sentences(cls, value: str) -> str:
        if count_sentences(value) > 3:
            raise ValueError("each beat must be at most 3 sentences")
        return value

    def as_text(self) -> str:
        return "\n\n".join([self.beat_1_claim, self.beat_2_evidence, self.beat_3_bottom_line])

    def cited_handles(self) -> set[str]:
        return extract_handles(self.as_text())


class CitationGroup(BaseModel):
    """One factual claim and the sources the model says support it."""

    claim: NonEmptyStr
    source_ids: list[str] = Field(min_length=1)

    @field_validator("source_ids")
    @classmethod
    def _handles_well_formed(cls, value: list[str]) -> list[str]:
        for handle in value:
            if not re.fullmatch(r"S\d+", handle):
                raise ValueError(f"citation handle {handle!r} is malformed")
        return value


class SynthesisOutput(BaseModel):
    """Structured-output schema for LLM call 2.

    Schema-level constraints catch shape errors. They do NOT establish
    grounding — that is ``evidence/validation.py``'s job, and it runs against
    the prompt's handle set and the database, neither of which the model can
    influence.
    """

    headline: NonEmptyStr
    verdict: Verdict
    verdict_qualifier: str | None = Field(
        default=None, description="Optional single clause, e.g. 'for sleep quality only'"
    )
    summary: NonEmptyStr
    body: ArticleBody
    citations: list[CitationGroup] = Field(default_factory=list)

    @field_validator("headline")
    @classmethod
    def _headline_at_most_12_words(cls, value: str) -> str:
        if len(value.split()) > 12:
            raise ValueError("headline must be at most 12 words")
        return value

    @field_validator("summary")
    @classmethod
    def _summary_at_most_2_sentences(cls, value: str) -> str:
        if count_sentences(value) > 2:
            raise ValueError("summary must be at most 2 sentences")
        return value

    @field_validator("verdict_qualifier")
    @classmethod
    def _qualifier_is_one_clause(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value.split()) > 15 or count_sentences(value) > 1:
            raise ValueError("verdict qualifier must be a single short clause")
        return value

    def all_cited_handles(self) -> set[str]:
        """Every handle the model emitted, inline or in the citations list."""
        handles = self.body.cited_handles()
        for group in self.citations:
            handles.update(group.source_ids)
        return handles


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class ValidationFailure(BaseModel):
    code: str = Field(
        description="hallucinated_handle | unresolvable_source | uncited_beat "
        "| verdict_exceeds_grade | malformed_body"
    )
    message: str
    detail: dict[str, object] = Field(default_factory=dict)


class ValidationReport(BaseModel):
    """Stored on the article and surfaced in the review UI.

    ``citations_resolved``/``citations_total`` is the '4/4 citations resolve'
    badge — computed once at draft time, not recomputed in the browser.
    """

    passed: bool
    citations_total: int
    citations_resolved: int
    best_evidence_grade: StudyType
    failures: list[ValidationFailure] = Field(default_factory=list)

    @property
    def badge(self) -> str:
        return f"{self.citations_resolved}/{self.citations_total} citations resolve"
