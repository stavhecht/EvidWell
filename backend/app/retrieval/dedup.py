"""Cross-provider identity: deciding which records are the same paper.

The same study routinely arrives from three providers carrying three different
identifier *subsets* — Europe PMC knows its PMID, Semantic Scholar knows its
DOI, PubMed knows both. Keying each record on a single preferred identifier
(DOI, else PMID, else title) cannot unify those: the DOI-only record and the
PMID-only record produce different keys and survive as two candidates, become
two ``sources`` rows, and are handed to the model under two handles.

That is not a cosmetic duplicate. ``rerank.assign_handles`` already guards the
same failure from the other direction — one paper seen twice under two names is
cited as two independent findings, manufacturing corroboration out of a single
study — in a product whose entire proposition is that its citations are real.

So identity is computed as a **union-find over every identifier a record
carries**, not as one key per record. A record holding both a DOI and a PMID
joins those two identifiers into one group, and any record carrying either one
then belongs to that group. One PubMed hit is enough to bridge the Europe PMC
and Semantic Scholar records of the same paper.

**Titles do not join groups.** A normalised title is used as an identity key
only for a record carrying no identifier at all (which the provider adapters
already refuse to emit, so it is a floor rather than a path). Bridging on title
would merge erratum notices with their parent paper and conference abstracts
with the full study — both of which repeat the title verbatim — and a wrong
merge is invisible afterwards, because the result simply looks like one paper.
Under-merging costs a duplicated prompt slot; over-merging silently deletes a
distinct study. The asymmetry decides it.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.domain.contracts import CandidatePaper
from app.domain.enums import EVIDENCE_RANK, SourceApi

#: Whose abstract to keep when a group disagrees, highest first.
#:
#: Length is the wrong primary rule. OpenAlex does not ship abstracts as text —
#: it ships an inverted index that ``_reconstruct_abstract`` rebuilds, so lost
#: punctuation and tokenisation artefacts are normal rather than exceptional,
#: and a reconstructed abstract is often *longer* than the clean one. That text
#: is both what gets embedded and what the model reads as evidence, so the
#: cleanest wins and length is only the tiebreaker.
PROVIDER_TRUST: dict[SourceApi, int] = {
    SourceApi.PUBMED: 3,
    SourceApi.EUROPE_PMC: 2,
    SourceApi.SEMANTIC_SCHOLAR: 1,
    SourceApi.OPENALEX: 0,
}


def identity_keys(paper: CandidatePaper) -> list[str]:
    """Every identifier this record can be recognised by.

    All of them, not the best one — carrying both a DOI and a PMID is exactly
    what lets a record bridge two groups that would otherwise stay apart.
    """
    keys = []
    if paper.doi:
        keys.append(f"doi:{paper.doi.strip().lower()}")
    if paper.pmid:
        keys.append(f"pmid:{paper.pmid.strip()}")
    # Title only when there is nothing better; see the module docstring on why
    # it must never join two records that do carry identifiers.
    return keys or [paper.dedup_key]


class _UnionFind:
    """Disjoint sets over identifier strings, with path compression."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        self._parent.setdefault(key, key)
        root = key
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[key] != root:
            self._parent[key], key = root, self._parent[key]
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def merge_candidates(
    papers_by_claim: dict[str, list[CandidatePaper]],
) -> dict[str, list[CandidatePaper]]:
    """Collapse duplicates across every claim at once, preserving claim order.

    Grouping is global rather than per claim on purpose. A paper answering two
    claims must end up as the *same object* in both lists, because everything
    downstream — the cache upsert, ``RankStage``'s id lookup, handle assignment
    — keys on ``dedup_key``, and a paper merged in one claim but not another
    would carry two different keys through the rest of the pipeline.

    Returns one merged record per group per claim, in the order that claim's
    candidates first appeared.
    """
    all_papers = [paper for papers in papers_by_claim.values() for paper in papers]

    union = _UnionFind()
    for paper in all_papers:
        keys = identity_keys(paper)
        for key in keys[1:]:
            union.union(keys[0], key)

    grouped: dict[str, list[CandidatePaper]] = {}
    for paper in all_papers:
        grouped.setdefault(union.find(identity_keys(paper)[0]), []).append(paper)

    merged = {root: merge_group(group) for root, group in grouped.items()}

    result: dict[str, list[CandidatePaper]] = {}
    for claim, papers in papers_by_claim.items():
        seen: set[str] = set()
        kept: list[CandidatePaper] = []
        for paper in papers:
            root = union.find(identity_keys(paper)[0])
            if root in seen:
                continue
            seen.add(root)
            kept.append(merged[root])
        result[claim] = kept
    return result


def unique_papers(papers_by_claim: dict[str, list[CandidatePaper]]) -> list[CandidatePaper]:
    """Every distinct paper across all claims, in first-seen order.

    Safe to call only on the output of ``merge_candidates``: it relies on one
    merged record per group, so ``dedup_key`` is a complete identity.
    """
    seen: set[str] = set()
    unique: list[CandidatePaper] = []
    for papers in papers_by_claim.values():
        for paper in papers:
            if paper.dedup_key in seen:
                continue
            seen.add(paper.dedup_key)
            unique.append(paper)
    return unique


def merge_group(group: Sequence[CandidatePaper]) -> CandidatePaper:
    """Fold one group of duplicate records into a single candidate.

    Takes the most trusted record as the base — which settles ``abstract`` and
    ``source_api`` together, so the stored provenance names the provider whose
    text we actually kept — then fills every field it is missing from the rest.
    Strongest-wins on study type is deliberate: one provider knowing a paper is
    an RCT is informative, another not knowing is not.
    """
    if len(group) == 1:
        return group[0]

    primary = max(
        group, key=lambda paper: (PROVIDER_TRUST.get(paper.source_api, 0), len(paper.abstract))
    )
    ordered = [primary, *(paper for paper in group if paper is not primary)]

    return primary.model_copy(
        update={
            "pmid": _first(paper.pmid for paper in ordered),
            "doi": _first(paper.doi for paper in ordered),
            "url": _first(paper.url for paper in ordered),
            "journal": _first(paper.journal for paper in ordered),
            "year": _first(paper.year for paper in ordered),
            "raw_study_type": _first(paper.raw_study_type for paper in ordered),
            "citation_count": max(
                (paper.citation_count or 0 for paper in ordered), default=0
            )
            or None,
            "study_type": max(
                (paper.study_type for paper in ordered),
                key=lambda study: EVIDENCE_RANK[study],
            ),
        }
    )


def _first[T](values: Iterable[T | None]) -> T | None:
    """The first value that is actually present."""
    return next((value for value in values if value is not None), None)
