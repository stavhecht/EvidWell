"""Europe PMC, Semantic Scholar and OpenAlex providers.

Grouped in one module because they share a shape — single REST call, JSON
response, map to CandidatePaper — unlike PubMed's two-call XML protocol, which
has its own module.

All three are Phase 4 work; ``settings.enabled_providers`` defaults to PubMed
alone. Role of each (DESIGN.md §4):

* **Europe PMC** — breadth, plus the open-access full text the v2 pass needs.
  Also the failover when PubMed rate-limits, since it indexes much of the same
  corpus.
* **Semantic Scholar** — citation counts and cross-domain coverage.
* **OpenAlex** — broad backstop. Fills gaps rather than leading: its metadata is
  the least reliable for study-type classification.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.domain.contracts import CandidatePaper
from app.domain.enums import SourceApi
from app.evidence.grading import classify_study_type
from app.retrieval.base import ProviderError, RateLimited, SearchQuery

logger = logging.getLogger(__name__)

EUROPE_PMC_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
SEMANTIC_SCHOLAR_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_URL = "https://api.openalex.org/works"

MIN_ABSTRACT_CHARS = 40


async def _get_json(
    http: httpx.AsyncClient,
    url: str,
    params: dict[str, Any],
    provider: str,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        response = await http.get(url, params=params, headers=headers)
    except httpx.HTTPError as exc:
        raise ProviderError(f"{provider} transport failure: {exc}") from exc

    if response.status_code == 429:
        retry_after = response.headers.get("retry-after")
        raise RateLimited(
            f"{provider} rate limit",
            retry_after=float(retry_after) if retry_after else None,
        )
    if response.status_code >= 400:
        raise ProviderError(f"{provider} returned {response.status_code}")

    try:
        return response.json()
    except ValueError as exc:
        raise ProviderError(f"{provider} returned non-JSON") from exc


def _clean_doi(doi: str | None) -> str | None:
    """Strip the URL prefixes providers inconsistently attach.

    Matters for dedup: 'https://doi.org/10.1/x' and '10.1/x' are the same paper
    but different dedup keys, so the same study would enter the prompt twice
    under two handles.
    """
    if not doi:
        return None
    cleaned = doi.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if cleaned.lower().startswith(prefix):
            cleaned = cleaned[len(prefix) :]
    return cleaned or None


class EuropePMCProvider:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    @property
    def source_api(self) -> SourceApi:
        return SourceApi.EUROPE_PMC

    async def search(self, query: SearchQuery) -> list[CandidatePaper]:
        terms = query.terms
        if query.reviews_only:
            terms = f'({terms}) AND PUB_TYPE:"review"'
        if query.min_year:
            terms = f"({terms}) AND PUB_YEAR:[{query.min_year} TO 3000]"

        payload = await _get_json(
            self._http,
            EUROPE_PMC_URL,
            {
                "query": terms,
                "format": "json",
                # 'core' is what includes abstracts; the default result type
                # omits them, which makes every record unusable here.
                "resultType": "core",
                "pageSize": query.max_results,
            },
            "europe_pmc",
        )

        papers: list[CandidatePaper] = []
        for record in payload.get("resultList", {}).get("result", []):
            abstract = (record.get("abstractText") or "").strip()
            title = (record.get("title") or "").strip()
            pmid = (record.get("pmid") or "").strip() or None
            doi = _clean_doi(record.get("doi"))

            if len(abstract) < MIN_ABSTRACT_CHARS or not title or (not pmid and not doi):
                continue

            publication_types = [
                entry
                for entry in (record.get("pubTypeList", {}) or {}).get("pubType", [])
                if isinstance(entry, str)
            ]
            year = record.get("pubYear")

            papers.append(
                CandidatePaper(
                    pmid=pmid,
                    doi=doi,
                    title=title,
                    abstract=abstract,
                    journal=(record.get("journalTitle") or None),
                    year=int(year) if str(year).isdigit() else None,
                    study_type=classify_study_type(publication_types, title, abstract),
                    raw_study_type="; ".join(publication_types) or None,
                    citation_count=record.get("citedByCount"),
                    url=(
                        f"https://europepmc.org/article/"
                        f"{record.get('source', 'MED')}/{record.get('id')}"
                    ),
                    source_api=SourceApi.EUROPE_PMC,
                )
            )
        return papers


class SemanticScholarProvider:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None) -> None:
        self._http = http
        self._api_key = api_key

    @property
    def source_api(self) -> SourceApi:
        return SourceApi.SEMANTIC_SCHOLAR

    async def search(self, query: SearchQuery) -> list[CandidatePaper]:
        if not self._api_key:
            # Unauthenticated access 429s under any real fan-out. Skipping is
            # better than burning the retry budget on a provider that cannot
            # serve us — recall degrades, which yields a more cautious verdict.
            logger.info("semantic scholar: no API key, skipping")
            return []

        params: dict[str, Any] = {
            "query": query.terms,
            "limit": min(query.max_results, 100),
            "fields": "title,abstract,year,venue,citationCount,externalIds,publicationTypes",
        }
        if query.min_year:
            params["year"] = f"{query.min_year}-"

        payload = await _get_json(
            self._http,
            SEMANTIC_SCHOLAR_URL,
            params,
            "semantic_scholar",
            headers={"x-api-key": self._api_key},
        )

        papers: list[CandidatePaper] = []
        for record in payload.get("data", []):
            abstract = (record.get("abstract") or "").strip()
            title = (record.get("title") or "").strip()
            # Identifiers live under externalIds, not at the top level.
            external = record.get("externalIds") or {}
            pmid = str(external.get("PubMed")).strip() if external.get("PubMed") else None
            doi = _clean_doi(external.get("DOI"))

            if len(abstract) < MIN_ABSTRACT_CHARS or not title or (not pmid and not doi):
                continue

            publication_types = record.get("publicationTypes") or []
            papers.append(
                CandidatePaper(
                    pmid=pmid,
                    doi=doi,
                    title=title,
                    abstract=abstract,
                    journal=record.get("venue") or None,
                    year=record.get("year"),
                    study_type=classify_study_type(publication_types, title, abstract),
                    raw_study_type="; ".join(publication_types) or None,
                    citation_count=record.get("citationCount"),
                    url=f"https://doi.org/{doi}" if doi else None,
                    source_api=SourceApi.SEMANTIC_SCHOLAR,
                )
            )
        return papers


class OpenAlexProvider:
    def __init__(self, http: httpx.AsyncClient, mailto: str | None = None) -> None:
        self._http = http
        # OpenAlex gives the polite pool — materially better latency — in
        # exchange for a contact address. Worth setting.
        self._mailto = mailto

    @property
    def source_api(self) -> SourceApi:
        return SourceApi.OPENALEX

    async def search(self, query: SearchQuery) -> list[CandidatePaper]:
        filters = ["has_abstract:true"]
        if query.min_year:
            filters.append(f"from_publication_date:{query.min_year}-01-01")
        if query.reviews_only:
            filters.append("type:review")

        params: dict[str, Any] = {
            "search": query.terms,
            "filter": ",".join(filters),
            "per-page": min(query.max_results, 50),
        }
        if self._mailto:
            params["mailto"] = self._mailto

        payload = await _get_json(self._http, OPENALEX_URL, params, "openalex")

        papers: list[CandidatePaper] = []
        for record in payload.get("results", []):
            abstract = _reconstruct_abstract(record.get("abstract_inverted_index"))
            title = (record.get("display_name") or "").strip()
            ids = record.get("ids") or {}
            doi = _clean_doi(ids.get("doi"))
            pmid = None
            if pubmed_url := ids.get("pmid"):
                pmid = str(pubmed_url).rstrip("/").rsplit("/", 1)[-1] or None

            if len(abstract) < MIN_ABSTRACT_CHARS or not title or (not pmid and not doi):
                continue

            work_type = record.get("type")
            publication_types = [work_type] if isinstance(work_type, str) else []
            venue = ((record.get("primary_location") or {}).get("source") or {}).get(
                "display_name"
            )

            papers.append(
                CandidatePaper(
                    pmid=pmid,
                    doi=doi,
                    title=title,
                    abstract=abstract,
                    journal=venue,
                    year=record.get("publication_year"),
                    study_type=classify_study_type(publication_types, title, abstract),
                    raw_study_type=work_type if isinstance(work_type, str) else None,
                    citation_count=record.get("cited_by_count"),
                    url=f"https://doi.org/{doi}" if doi else None,
                    source_api=SourceApi.OPENALEX,
                )
            )
        return papers


def _reconstruct_abstract(inverted_index: dict[str, list[int]] | None) -> str:
    """Rebuild an abstract from OpenAlex's token -> positions map.

    OpenAlex does not ship abstracts as strings; it ships an inverted index.
    Records that reconstruct to nothing are dropped by the caller.
    """
    if not inverted_index:
        return ""
    positions: list[tuple[int, str]] = [
        (position, token)
        for token, token_positions in inverted_index.items()
        for position in token_positions
    ]
    positions.sort()
    return " ".join(token for _, token in positions).strip()
