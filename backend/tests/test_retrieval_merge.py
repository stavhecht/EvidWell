"""Cross-provider merge in the retrieve stage.

Running more than one scholarly API is only worth anything if the same paper
arriving twice becomes one better record rather than two studies. Two things
have to hold:

* **No double-counting.** Two handles for one paper makes a single study look
  like corroboration, which is the exact illusion this product exists to
  puncture. It also inflates ``evidence_grade`` inputs.
* **Best-of wins per field.** PubMed carries real publication types; OpenAlex
  carries a single coarse ``type`` string but often a longer abstract and a
  citation count. Whichever provider knew more should decide each field.
"""

from __future__ import annotations

from app.domain.contracts import CandidatePaper
from app.domain.enums import SourceApi, StudyType
from app.pipeline.steps.retrieve import RetrieveStage


def _paper(**overrides: object) -> CandidatePaper:
    defaults: dict[str, object] = {
        "title": "Ashwagandha and cortisol in adults",
        "abstract": "A" * 200,
        "source_api": SourceApi.PUBMED,
    }
    return CandidatePaper(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_same_paper_from_two_providers_collapses_to_one() -> None:
    pubmed = _paper(pmid="123", doi="10.1000/xyz", source_api=SourceApi.PUBMED)
    openalex = _paper(pmid="123", doi="10.1000/xyz", source_api=SourceApi.OPENALEX)

    assert len(RetrieveStage._dedup([pubmed, openalex])) == 1


def test_merge_matches_on_pmid_when_only_one_provider_reports_a_doi() -> None:
    """The case a single dedup key gets wrong.

    PubMed has no DOI for this record, so it keys on ``pmid:123``; OpenAlex has
    both, so it keys on ``doi:``. Keying on the strongest identifier each
    happens to hold would file one paper as two studies.
    """
    pubmed = _paper(pmid="123", doi=None, source_api=SourceApi.PUBMED)
    openalex = _paper(pmid="123", doi="10.1000/xyz", source_api=SourceApi.OPENALEX)

    merged = RetrieveStage._dedup([pubmed, openalex])

    assert len(merged) == 1
    # And the surviving record gained the identifier it was missing.
    assert merged[0].doi == "10.1000/xyz"
    assert merged[0].pmid == "123"


def test_merge_keeps_the_stronger_study_type_whichever_provider_had_it() -> None:
    """OpenAlex reports 'article'; PubMed knows it is an RCT. PubMed wins.

    This is the field that matters most: study type feeds the grade bonus in
    rerank and caps verdict confidence under invariant #3.
    """
    openalex = _paper(
        doi="10.1000/xyz", study_type=StudyType.UNKNOWN, source_api=SourceApi.OPENALEX
    )
    pubmed = _paper(
        doi="10.1000/xyz", study_type=StudyType.RCT, source_api=SourceApi.PUBMED
    )

    # Order must not matter — the fan-out returns providers in arbitrary order.
    for order in ([openalex, pubmed], [pubmed, openalex]):
        merged = RetrieveStage._dedup(order)
        assert len(merged) == 1
        assert merged[0].study_type is StudyType.RCT


def test_merge_takes_the_longer_abstract_and_the_citation_count() -> None:
    short = _paper(doi="10.1000/xyz", abstract="A" * 200, citation_count=None)
    long = _paper(
        doi="10.1000/xyz",
        abstract="B" * 900,
        citation_count=42,
        source_api=SourceApi.OPENALEX,
    )

    merged = RetrieveStage._dedup([short, long])

    assert len(merged) == 1
    assert merged[0].abstract == "B" * 900
    assert merged[0].citation_count == 42


def test_three_providers_with_disjoint_identifier_subsets_still_collapse() -> None:
    """Transitivity: A shares a PMID with B, B shares a DOI with C."""
    a = _paper(pmid="123", doi=None, source_api=SourceApi.PUBMED)
    b = _paper(pmid="123", doi="10.1000/xyz", source_api=SourceApi.OPENALEX)
    c = _paper(pmid=None, doi="10.1000/xyz", source_api=SourceApi.EUROPE_PMC)

    assert len(RetrieveStage._dedup([a, b, c])) == 1


def test_genuinely_different_papers_are_not_merged() -> None:
    first = _paper(pmid="123", doi="10.1000/aaa", title="Ashwagandha and cortisol")
    second = _paper(pmid="456", doi="10.1000/bbb", title="Rhodiola and fatigue")

    assert len(RetrieveStage._dedup([first, second])) == 2
