"""Regression cover for the ON CONFLICT cardinality rule in ``SourceCache``.

Candidates are collected per claim, so one paper routinely arrives once per
claim it answers. Postgres rejects an ``INSERT ... ON CONFLICT DO UPDATE``
whose statement proposes two rows sharing a conflict key, so ``upsert_many``
has to collapse them itself — the rank stage flattens the per-claim dict
without deduping first.
"""

from __future__ import annotations

from typing import Any

from app.domain.contracts import CandidatePaper, SourceApi, StudyType
from app.retrieval.cache import SourceCache


class _CapturingSession:
    """Records the rows each statement proposes; returns nothing."""

    def __init__(self) -> None:
        self.proposed: list[list[dict[str, Any]]] = []

    async def execute(self, statement: Any) -> Any:
        # ``_multi_values`` is a 1-tuple holding the list of proposed rows, each
        # keyed by Column rather than by name.
        for group in statement._multi_values:
            self.proposed.append(
                [{column.name: value for column, value in row.items()} for row in group]
            )

        class _Result:
            def all(self) -> list[Any]:
                return []

        return _Result()


def _paper(*, doi: str | None = None, pmid: str | None = None) -> CandidatePaper:
    return CandidatePaper(
        pmid=pmid,
        doi=doi,
        title=f"Paper {doi or pmid}",
        abstract="An abstract long enough to be meaningful for the cache test.",
        journal="J. Test",
        year=2024,
        study_type=StudyType.RCT,
        raw_study_type="Randomized Controlled Trial",
        citation_count=3,
        url="https://example.org/paper",
        source_api=SourceApi.PUBMED,
    )


async def test_upsert_many_collapses_paper_repeated_across_claims() -> None:
    """The same DOI twice must not become two rows in one statement."""
    session = _CapturingSession()
    cache = SourceCache(session, embedder=None)  # type: ignore[arg-type]

    duplicated = _paper(doi="10.1000/abc")
    await cache.upsert_many([duplicated, duplicated, _paper(doi="10.1000/xyz")])

    dois = [row["doi"] for batch in session.proposed for row in batch]
    assert sorted(dois) == ["10.1000/abc", "10.1000/xyz"]


async def test_upsert_many_collapses_case_differing_dois() -> None:
    """The unique index is on ``lower(doi)``, so casing cannot split a key."""
    session = _CapturingSession()
    cache = SourceCache(session, embedder=None)  # type: ignore[arg-type]

    await cache.upsert_many([_paper(doi="10.1000/ABC"), _paper(doi="10.1000/abc")])

    dois = [row["doi"] for batch in session.proposed for row in batch]
    assert len(dois) == 1


async def test_upsert_many_collapses_repeated_pmids() -> None:
    """DOI-less papers conflict on ``pmid`` and need the same treatment."""
    session = _CapturingSession()
    cache = SourceCache(session, embedder=None)  # type: ignore[arg-type]

    await cache.upsert_many([_paper(pmid="12345"), _paper(pmid="12345")])

    pmids = [row["pmid"] for batch in session.proposed for row in batch]
    assert pmids == ["12345"]
