"""What Europe PMC knows about a paper's identifiers, looked up by PMID or DOI.

Two callers, one request shape:

* ``RetrieveStage`` fills in the PMID of a paper that arrived with only a DOI
  (``pmids_for_dois``). OpenAlex and Semantic Scholar often know a paper by DOI
  alone, and without a PMID it is invisible to the full-text lookup, to the
  retraction sweep, and to union-find against a PubMed-only record of the same
  paper.
* ``FullTextStage`` asks which papers have an open-access full text
  (``records``), by PMID where there is one and by DOI where there is not.

A DOI with no PMID here usually has none anywhere: measured 2026-09-29, all nine
DOI-only papers in the cache were missing from PubMed, Europe PMC and NCBI's ID
converter alike (journals MEDLINE does not index). A lookup that finds nothing
leaves the paper exactly as it was.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.retrieval.base import ProviderError, RateLimited
from app.retrieval.throttle import HttpClient

EUROPE_PMC_REST = "https://www.ebi.ac.uk/europepmc/webservices/rest"

#: Identifiers per search request. Quoted DOIs are long, and the query rides in
#: the URL.
_BATCH = 50


@dataclass(frozen=True, slots=True)
class EuropePMCRecord:
    pmid: str | None
    doi: str | None  # lower-cased
    pmcid: str | None
    open_access: bool


class PmidResolver(Protocol):
    async def pmids_for_dois(self, dois: list[str]) -> dict[str, str]:
        """Lower-cased DOI -> PMID, for every DOI that has exactly one PMID."""
        ...


class EuropePMCIdentifiers:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    async def records(
        self, *, pmids: Iterable[str] = (), dois: Iterable[str] = ()
    ) -> list[EuropePMCRecord]:
        """Every Europe PMC record matching one of ``pmids`` or ``dois``."""
        terms = [f"(SRC:MED AND EXT_ID:{pmid})" for pmid in sorted(set(pmids))]
        # A DOI holding a quote would break out of its phrase. None do in
        # practice; skipping one costs that paper a lookup, nothing more.
        terms += [f'DOI:"{doi}"' for doi in sorted({d.lower() for d in dois}) if '"' not in doi]

        found: list[EuropePMCRecord] = []
        for start in range(0, len(terms), _BATCH):
            response = await europe_pmc_get(
                self._http,
                f"{EUROPE_PMC_REST}/search",
                {
                    "query": " OR ".join(terms[start : start + _BATCH]),
                    "format": "json",
                    "resultType": "lite",
                    "pageSize": 1000,
                },
            )
            try:
                results = response.json()["resultList"]["result"]
            except (ValueError, KeyError) as exc:
                raise ProviderError("europe_pmc search returned an unexpected body") from exc
            found.extend(
                EuropePMCRecord(
                    pmid=record.get("pmid") or None,
                    doi=(record.get("doi") or "").lower() or None,
                    pmcid=record.get("pmcid") or None,
                    open_access=record.get("isOpenAccess") == "Y",
                )
                for record in results
            )
        return found

    async def pmids_for_dois(self, dois: list[str]) -> dict[str, str]:
        wanted = {doi.lower() for doi in dois}
        pmids: dict[str, set[str]] = {}
        for record in await self.records(dois=wanted):
            # Matched on the DOI the record itself carries, never on the fact
            # that the search returned it: a phrase query can match loosely.
            if record.doi in wanted and record.pmid:
                pmids.setdefault(record.doi, set()).add(record.pmid)
        # Two PMIDs for one DOI is a question, not an answer. Guessing would
        # merge two papers in union-find and delete one of them.
        return {doi: next(iter(found)) for doi, found in pmids.items() if len(found) == 1}


async def europe_pmc_get(
    http: HttpClient, url: str, params: dict[str, Any] | None
) -> httpx.Response:
    try:
        response = await http.get(url, params=params)
    except httpx.HTTPError as exc:
        raise ProviderError(f"europe_pmc transport failure: {exc}") from exc
    if response.status_code == 429:
        raise RateLimited("europe_pmc rate limit")
    if response.status_code >= 400:
        raise ProviderError(f"europe_pmc returned {response.status_code} for {url}")
    return response
