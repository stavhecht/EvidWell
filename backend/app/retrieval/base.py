"""Scholarly provider interface.

**The scholarly APIs are the primary search index.** pgvector is a re-ranking
and caching layer on top of what these return — there is no attempt to
pre-index PubMed, and adding one would be a different (and much worse) system.

Every provider normalises its own response shape into ``CandidatePaper`` at its
own boundary, so nothing downstream knows which API a paper came from except by
reading ``source_api``.

Excluded by design: Google Scholar and any form of scraping. SerpApi is
reserved as a later fallback if genuine coverage gaps appear in Phase 4, and is
not part of the MVP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.domain.contracts import CandidatePaper
from app.domain.enums import SourceApi


@dataclass(frozen=True, slots=True)
class SearchQuery:
    """One provider-agnostic search, produced by a QueryStrategy."""

    claim: str
    terms: str
    #: Ask the provider to restrict to reviews/meta-analyses. Set on the first
    #: of the two passes described in retrieval/query_builder.py.
    reviews_only: bool = False
    max_results: int = 25
    min_year: int | None = None


class ScholarlyProvider(Protocol):
    """One scholarly API."""

    @property
    def source_api(self) -> SourceApi: ...

    async def search(self, query: SearchQuery) -> list[CandidatePaper]:
        """Return normalised candidates for a query.

        Implementations must:

        * Drop any record with neither a PMID nor a DOI. An unresolvable paper
          can never satisfy the grounding rule, so it is discarded here rather
          than carried forward to fail validation later.
        * Drop records with an empty abstract — there is nothing to embed and
          nothing for the model to ground on.
        * Populate ``raw_study_type`` with the provider's own publication-type
          string, even when classification into ``StudyType`` fails. It lets the
          classifier be re-run over the cache without re-fetching.
        * Return an empty list rather than raising when the provider has no
          results. Raise only on transport or quota failure.
        """
        ...


class ProviderError(RuntimeError):
    """Transport, quota, or malformed-response failure from a provider."""


class RateLimited(ProviderError):
    """Provider asked us to back off.

    Carries ``retry_after`` so the fan-out can decide between waiting and
    failing over to another provider. PubMed without an API key is 3 req/s,
    which this will hit routinely.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after
