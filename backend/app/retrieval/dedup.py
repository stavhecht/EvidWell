"""Decide which records from different providers are the same paper.

Providers know different identifiers for the same paper: Europe PMC may send
only the PMID, Semantic Scholar only the DOI, PubMed both. Keying each record
on one identifier would keep the DOI-only and PMID-only records apart, and the
model would then cite one study as two independent findings.

So records are grouped with a union-find over *every* identifier they carry:
a record with both a DOI and a PMID links the two, and any record carrying
either one joins that group.

Titles never link records that have identifiers. Errata and conference
abstracts repeat their parent's title, and merging those would silently delete
a real study. A title is used only for a record with no identifier at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from app.domain.contracts import CandidatePaper
from app.domain.enums import EVIDENCE_RANK, SourceApi

#: Whose abstract to keep when duplicates disagree, highest first. Cleanest
#: text wins, not longest: OpenAlex rebuilds abstracts from a word index, so
#: its version is often longer and damaged.
PROVIDER_TRUST: dict[SourceApi, int] = {
    SourceApi.PUBMED: 3,
    SourceApi.EUROPE_PMC: 2,
    SourceApi.SEMANTIC_SCHOLAR: 1,
    SourceApi.OPENALEX: 0,
}


def identity_keys(paper: CandidatePaper) -> list[str]:
    """Every identifier this record can be recognised by (not just the best one)."""
    keys = []
    if paper.doi:
        keys.append(f"doi:{paper.doi.strip().lower()}")
    if paper.pmid:
        keys.append(f"pmid:{paper.pmid.strip()}")
    return keys or [paper.dedup_key]  # the title, only when there is nothing else


class _UnionFind:
    """Groups of identifier strings. ``union`` joins two groups; ``find`` names a group."""

    def __init__(self) -> None:
        self._parent: dict[str, str] = {}

    def find(self, key: str) -> str:
        """The group's root key. Also points every key on the way straight at
        the root, so the next lookup is one step."""
        root = self._parent.setdefault(key, key)
        while self._parent[root] != root:
            root = self._parent[root]
        while key != root:
            next_key = self._parent[key]
            self._parent[key] = root
            key = next_key
        return root

    def union(self, left: str, right: str) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[right_root] = left_root


def merge_candidates(
    papers_by_claim: dict[str, list[CandidatePaper]],
) -> dict[str, list[CandidatePaper]]:
    """Collapse duplicates across every claim at once, keeping each claim's order.

    Global rather than per claim, so a paper answering two claims comes out as
    the same merged record (and the same ``dedup_key``) in both lists.
    """
    all_papers = [paper for papers in papers_by_claim.values() for paper in papers]

    groups = _UnionFind()
    for paper in all_papers:
        first, *rest = identity_keys(paper)
        for key in rest:
            groups.union(first, key)

    def group_of(paper: CandidatePaper) -> str:
        return groups.find(identity_keys(paper)[0])

    members: dict[str, list[CandidatePaper]] = {}
    for paper in all_papers:
        members.setdefault(group_of(paper), []).append(paper)
    merged = {root: merge_group(group) for root, group in members.items()}

    result: dict[str, list[CandidatePaper]] = {}
    for claim, papers in papers_by_claim.items():
        roots = dict.fromkeys(group_of(paper) for paper in papers)  # ordered, unique
        result[claim] = [merged[root] for root in roots]
    return result


def unique_papers(papers_by_claim: dict[str, list[CandidatePaper]]) -> list[CandidatePaper]:
    """Every distinct paper across all claims, in first-seen order.

    Call only on ``merge_candidates`` output, where ``dedup_key`` is a full identity.
    """
    unique: dict[str, CandidatePaper] = {}
    for papers in papers_by_claim.values():
        for paper in papers:
            unique.setdefault(paper.dedup_key, paper)
    return list(unique.values())


def merge_group(group: Sequence[CandidatePaper]) -> CandidatePaper:
    """Fold one group of duplicate records into a single paper.

    The most trusted provider's record is the base (its abstract and
    ``source_api`` are kept together); missing fields are filled from the
    others. The strongest study type wins: one provider knowing a paper is an
    RCT is informative, another not knowing is not.
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
            "citation_count": max((paper.citation_count or 0 for paper in ordered), default=0)
            or None,
            "study_type": max(
                (paper.study_type for paper in ordered), key=lambda study: EVIDENCE_RANK[study]
            ),
        }
    )


def _first[T](values: Iterable[T | None]) -> T | None:
    """The first value that is actually present."""
    return next((value for value in values if value is not None), None)
