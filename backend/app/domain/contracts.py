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
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints, field_validator, model_validator

from app.domain.enums import SourceApi, StudyType, Verdict

#: One inline citation marker as the model emits it: ``[S1]``, or ``[S1, S5]``
#: when several sources back one statement.
#:
#: **The comma form is accepted deliberately.** The canonical multi-source
#: syntax is adjacent brackets (``[S1][S5]``), which is what ``doc_to_plain_text``
#: regenerates — but it is not a form a model produces unprompted, and the
#: synthesis prompt only ever showed a single handle. A model asked to cite
#: three sources writes ``[S1, S5, S8]``, and rejecting that cost a whole draft:
#: the body failed to parse, the article was written ``validation_failed``, and
#: it never reached the review queue.
#:
#: **This is the only definition of a marker, and tiptap.py builds on it.** The
#: parser and the handle extractor disagreeing is worse than either being
#: strict: handles inside a marker the extractor cannot read are absent from
#: ``ArticleBody.cited_handles()``, so ``was_cited`` is false for sources the article
#: visibly cites, and ``check_beats_are_cited`` reports an uncited beat that is
#: cited on the page.
CITATION_MARKER_PATTERN = r"\[S\d+(?:\s*,\s*S\d+)*\]"
CITATION_MARKER_RE = re.compile(CITATION_MARKER_PATTERN)

#: A handle within a marker. Only ever applied to ``CITATION_MARKER_RE``
#: matches, so it does not need to guard against prose ("the S1 group").
_HANDLE_IN_MARKER_RE = re.compile(r"S\d+")

#: A citation handle in canonical form. Expressed as a schema-level pattern
#: rather than a validator on purpose: ``model_json_schema()`` carries
#: ``pattern`` into the structured-output grammar, so Ollama's sampler cannot
#: emit a malformed handle in the first place. A ``@field_validator`` is
#: invisible to the schema and only ever catches the mistake after generation.
#:
#: Written ``[0-9]`` and NOT ``\d`` deliberately. Ollama compiles this pattern
#: into a GBNF grammar and its converter does not understand the ``\d`` escape:
#: with ``\d`` the whole request fails at 400 "failed to parse grammar", which
#: breaks every synthesis call rather than just a malformed one. Verified
#: against llama3.1:8b — see tests/test_content.py.
CitationHandle = Annotated[str, StringConstraints(pattern=r"^S[0-9]+$")]

#: The shorthand a model reaches for when every source backs the same claim:
#: "S1-S8" as one string instead of eight handles. Covers the dash variants
#: models actually emit (hyphen, en dash, em dash) and the "S1-8" short form.
_HANDLE_RANGE_RE = re.compile(r"S(\d+)\s*[-\u2013\u2014]\s*S?(\d+)")

#: Ceiling on range expansion. Retrieval keeps single digits of sources per
#: claim, so a wider span is a confused model rather than a real citation list.
#: Leaving it unexpanded lets the pattern reject it instead of inventing
#: hundreds of handles we never retrieved.
_MAX_RANGE_SPAN = 50

# A rough sentence splitter. Deliberately simple: it is used to enforce a
# *ceiling*, so over-counting on an edge case (an abbreviation like "e.g.")
# fails safe by rejecting a too-long draft rather than admitting one.
_SENTENCE_RE = re.compile(r"[.!?](?:\s|$)")


def count_sentences(text: str) -> int:
    return len([s for s in _SENTENCE_RE.split(text.strip()) if s.strip()])


def extract_handles(text: str) -> set[str]:
    """Every citation handle appearing inline in a body of text.

    Two steps rather than one capturing regex, because a marker may hold
    several handles (``[S1, S5]``) and ``findall`` returns one group per match.
    """
    return {
        handle
        for marker in CITATION_MARKER_RE.findall(text)
        for handle in _HANDLE_IN_MARKER_RE.findall(marker)
    }


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
        """Cross-provider identity.

        DOI, then PMID, then a normalised title. The same paper routinely
        arrives from three providers with three different identifier subsets,
        so the fallback chain matters more than it looks.
        """
        if self.doi:
            return f"doi:{self.doi.strip().lower()}"
        if self.pmid:
            return f"pmid:{self.pmid.strip()}"
        return "title:" + re.sub(r"[^a-z0-9]+", "", self.title.lower())


class CachedCandidate(BaseModel):
    """A candidate paired with the ``sources`` row it was written to.

    RetrieveStage produces these; RankStage consumes them. The pairing exists
    on the context because RankStage used to rebuild it by re-upserting every
    candidate a second time — 100 extra statements to recover ids the previous
    stage already held in memory, most of them single-row UPDATEs.

    A model rather than a parallel ``dedup_key -> source_id`` map, so the two
    halves cannot drift: a candidate is either carried with its id or not
    carried at all. The map version silently drops any candidate missing from
    it, and a source dropped between retrieval and ranking is invisible in the
    output — it just looks like thinner evidence.
    """

    source_id: str
    paper: CandidatePaper


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
    #: Which target claims this source was retrieved for — a list, because one
    #: paper answering two claims appears **once** in the prompt under one
    #: handle (see ``assign_handles``), and losing that would show the model
    #: the same study twice as though it were two findings.
    #:
    #: Carried purely so validation can apply the evidence quorum *per claim*.
    #: Counted article-wide, one well-supported claim lets a thin one ride
    #: along on its sources, and nothing downstream can see that happened.
    #:
    #: **Required, and non-empty.** Defaulting it to ``[]`` was tried: a source
    #: attributed to no claim supports no claim, so every draft failed the
    #: quorum check for reasons that pointed at the verdict rather than at the
    #: missing field. Fail-closed is the right direction, but a required field
    #: fails at construction instead, where the actual mistake is.
    claims: list[str] = Field(min_length=1)


class SynthesisInput(BaseModel):
    """What the synthesis stage was given, carried forward for validation.

    ``sources`` may be **empty**, and that is a meaningful state rather than a
    missing guard: a topic with no usable literature gets the deterministic
    no-evidence article (``services/no_evidence.py``), and validation still
    needs a payload on the context to check against — an empty handle set is
    the correct thing for it to find, because an empty one is what the article
    was written from.

    The guarantee that a *generative* call never runs on zero sources lives in
    ``SynthesizeStage``, which branches to the template before reaching the
    client. Enforcing it here instead would make the honest "no evidence"
    article unrepresentable, which is how it came to crash the pipeline.
    """

    product: str
    target_claims: list[str]
    sources: list[PromptSource] = Field(default_factory=list)

    @property
    def handle_set(self) -> set[str]:
        return {s.handle for s in self.sources}


class ArticleSection(BaseModel):
    """One titled stretch of the evidence discussion, between beats 2 and 3.

    Sections are what let an article run to a three-to-five minute read without
    becoming three unbroken blocks of prose. They are **optional and have no
    minimum** — that is the whole point. DESIGN.md §6's rule is that length is a
    ceiling and never a floor, so a `min_length` here would be a padding
    instruction: an article resting on one small trial would be obliged to
    invent two more things to say about it.

    ``body`` may hold blank-line-separated paragraphs; ``tiptap.py`` splits them
    into sibling paragraph nodes.
    """

    heading: NonEmptyStr = Field(description="A plain descriptive label, not a claim")
    body: NonEmptyStr = Field(description="The section's prose, with inline citations")

    @field_validator("heading")
    @classmethod
    def _heading_at_most_8_words(cls, value: str) -> str:
        if len(value.split()) > 8:
            raise ValueError("a section heading must be at most 8 words")
        return value

    @field_validator("body")
    @classmethod
    def _body_at_most_8_sentences(cls, value: str) -> str:
        if count_sentences(value) > 8:
            raise ValueError("a section must be at most 8 sentences")
        return value


class ArticleBody(BaseModel):
    """Three beats, plus the optional sections that sit between 2 and 3.

    Every bound here is a ceiling, not a target. A beat that would be honest at
    one sentence must stay one sentence, and an article with one usable trial
    should carry no sections at all — see DESIGN.md §6. Nothing in this system
    pads to length.

    The three beats stayed named fields when sections arrived, rather than
    becoming a list. Three things address them by name: ``derive_card`` reads
    beat 1 for the feed excerpt, ``check_beats_are_cited`` reads beat 2, and
    ``PersistStage`` places the generated lead image above beat 1. Reshaping the
    spine would have rippled into all three and bought nothing the sections do
    not already give.
    """

    beat_1_claim: NonEmptyStr = Field(description="What it claims to do")
    beat_2_evidence: NonEmptyStr = Field(description="What the research actually shows")
    sections: list[ArticleSection] = Field(
        default_factory=list,
        max_length=5,
        description="Optional titled sections expanding on the evidence",
    )
    beat_3_bottom_line: NonEmptyStr = Field(description="Bottom line / caveat")

    @field_validator("beat_1_claim")
    @classmethod
    def _claim_at_most_four_sentences(cls, value: str) -> str:
        if count_sentences(value) > 4:
            raise ValueError("the claim beat must be at most 4 sentences")
        return value

    @field_validator("beat_2_evidence", "beat_3_bottom_line")
    @classmethod
    def _at_most_five_sentences(cls, value: str) -> str:
        if count_sentences(value) > 5:
            raise ValueError("this beat must be at most 5 sentences")
        return value

    def as_text(self) -> str:
        """Every word of the body, in reading order.

        Sections are included because ``cited_handles`` is built on this: a
        section's citations missing from the handle set would make ``was_cited``
        false for sources the article visibly cites, and would leave the
        strongest evidence in the article invisible to validation.
        """
        parts = [self.beat_1_claim, self.beat_2_evidence]
        for section in self.sections:
            parts.extend([section.heading, section.body])
        parts.append(self.beat_3_bottom_line)
        return "\n\n".join(parts)

    def cited_handles(self) -> set[str]:
        return extract_handles(self.as_text())


class CitationGroup(BaseModel):
    """One factual claim and the sources the model says support it."""

    claim: NonEmptyStr
    source_ids: list[CitationHandle] = Field(min_length=1)

    @field_validator("source_ids", mode="before")
    @classmethod
    def _normalise_handles(cls, value: Any) -> Any:
        """Expand range and comma shorthand into individual handles.

        Runs *before* the pattern check, so a model that collapses "S1", "S2",
        … "S8" into the single string "S1-S8" gets eight handles rather than a
        validation failure that fails the whole run.

        This only reshapes what the model already said; it cannot make a handle
        legitimate. Grounding is still established in ``evidence/validation.py``
        against the prompt's handle set and the database, so an expansion that
        yields a handle we never retrieved is caught there and the draft is
        discarded — the guarantee in this module's docstring is unchanged.
        """
        if not isinstance(value, list):
            return value

        expanded: list[Any] = []
        for entry in value:
            if not isinstance(entry, str):
                expanded.append(entry)
                continue
            for part in (piece.strip() for piece in entry.split(",")):
                if not part:
                    continue
                match = _HANDLE_RANGE_RE.fullmatch(part)
                if match is not None:
                    start, end = int(match.group(1)), int(match.group(2))
                    if 0 <= end - start < _MAX_RANGE_SPAN:
                        expanded.extend(f"S{n}" for n in range(start, end + 1))
                        continue
                expanded.append(part)

        # Order-preserving dedupe: "S1-S3" alongside a stray "S2" is one source
        # list, not a repeated citation. These lists are single digits long.
        deduped: list[Any] = []
        for handle in expanded:
            if handle not in deduped:
                deduped.append(handle)
        return deduped


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
# Illustration — generated imagery
# ---------------------------------------------------------------------------


class GeneratedImage(BaseModel):
    """One render, already written to the media store.

    ``src`` is the path a document may hold — it comes back from
    ``services/media.py::store_image``, so it satisfies ``MEDIA_SRC_RE`` by
    construction rather than by a later check.

    **Provenance is per frame, and that is a deliberate move down from
    ``Illustration``.** ``prompt``, ``negative_prompt``, ``model`` and ``seed``
    were shared fields on the pair for as long as the only way to draw either
    frame was to draw both. A reviewer can now redraw one on its own, and a
    single stored prompt would then be a true record of one picture and a false
    record of the other — the kind of quietly wrong provenance that is worse
    than none, because it still answers when asked.

    They are stored rather than recomputed because ``imagery/prompt.py`` is
    going to change and "which words produced this picture" stops being
    answerable the moment it does. Provenance outranks tidiness here, the same
    argument as the retracted-source rows.

    The three string fields default to empty so rows written before the move
    still validate; ``Illustration`` pushes their old shared values down into
    the frames on the way in, so nothing is actually lost.
    """

    src: str
    alt: str
    width: int
    height: int
    seed: int
    prompt: str = ""
    negative_prompt: str = ""
    #: Provider-namespaced, e.g. ``huggingface/black-forest-labs/FLUX.1-schnell``.
    model: str = ""


class Illustration(BaseModel):
    """Both pictures an article has: the one in its body, and the tile's.

    **They are not one photograph at two aspect ratios**, and they are no
    longer even guaranteed to come from one generation — a reviewer can redraw
    either alone. They are two renders of the same subject under the same
    locked, claim-free treatment; see ``services/illustration.py`` for the
    measurement that killed the stronger claim.

    What survives all of that is the property worth having, and
    ``services/card.py`` is what enforces it: the cover reaches the feed only
    while ``lead.src`` is still the document's first image, so the tile can
    never show a picture belonging to an article this one has stopped being.
    Neither frame asserts anything, so neither can assert what the other does
    not — which is why redrawing one and not the other is safe.
    """

    lead: GeneratedImage
    cover: GeneratedImage

    @model_validator(mode="before")
    @classmethod
    def _carry_legacy_shared_fields(cls, value: Any) -> Any:
        """Push a pre-split row's shared ``prompt``/``model`` onto both frames.

        Rows written while the pair shared one prompt keep it at the top level,
        where nothing reads it any more. Dropping it would silently discard the
        only record of how those two pictures were made, on the first partial
        redraw — so it is moved down instead. A frame that carries its own
        value always wins, which is what makes this safe to run on new rows too.
        """
        if not isinstance(value, dict):
            return value
        shared = {
            key: value[key]
            for key in ("prompt", "negative_prompt", "model")
            if isinstance(value.get(key), str)
        }
        if not shared:
            return value

        patched = dict(value)
        for frame in ("lead", "cover"):
            existing = patched.get(frame)
            if isinstance(existing, dict):
                patched[frame] = {**shared, **existing}
        return patched


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
