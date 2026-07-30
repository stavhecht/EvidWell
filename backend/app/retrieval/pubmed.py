"""PubMed E-utilities provider — the primary recall source.

Primary because its publication-type metadata is the cleanest study-type signal
available anywhere, and study type is what caps our verdict confidence. The
other providers give breadth; this one gives grading.

Two-call protocol: ``esearch`` returns PMIDs for a query, ``efetch`` returns
records for those PMIDs. Both are rate-limited (3 req/s anonymous, 10 req/s
with an API key — set ``PUBMED_API_KEY``).
"""

from __future__ import annotations

import logging
from xml.etree import ElementTree

import httpx

from app.domain.contracts import CandidatePaper
from app.domain.enums import SourceApi
from app.evidence.grading import classify_study_type
from app.retrieval.base import ProviderError, RateLimited, SearchQuery

logger = logging.getLogger(__name__)

ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"

#: PubMed query fragment restricting to reviews and meta-analyses. Appended for
#: the review-biased first pass — see query_builder.py for why retrieval is
#: biased at the query layer and not only at the ranking layer.
REVIEW_FILTER = '(systematic[sb] OR "meta-analysis"[pt] OR "systematic review"[pt])'


class PubMedProvider:
    def __init__(self, http: httpx.AsyncClient, api_key: str | None = None) -> None:
        self._http = http
        self._api_key = api_key

    @property
    def source_api(self) -> SourceApi:
        return SourceApi.PUBMED

    def _auth_params(self) -> dict[str, str]:
        return {"api_key": self._api_key} if self._api_key else {}

    async def search(self, query: SearchQuery) -> list[CandidatePaper]:
        """Search PubMed and return normalised candidates.

        Raises:
            RateLimited: HTTP 429, or the E-utilities "API rate limit exceeded"
                body that arrives with a 200 status.
            ProviderError: transport failure or unparseable XML.
        """
        pmids = await self._esearch(query)
        if not pmids:
            return []
        return await self._efetch(pmids)

    async def _esearch(self, query: SearchQuery) -> list[str]:
        terms = query.terms
        if query.reviews_only:
            terms = f"({terms}) AND {REVIEW_FILTER}"

        params: dict[str, str] = {
            "db": "pubmed",
            "term": terms,
            "retmax": str(query.max_results),
            "retmode": "json",
            "sort": "relevance",
            **self._auth_params(),
        }
        if query.min_year:
            params["mindate"] = str(query.min_year)
            params["maxdate"] = "3000"
            params["datetype"] = "pdat"

        try:
            response = await self._http.get(ESEARCH_URL, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"pubmed esearch transport failure: {exc}") from exc

        self._raise_for_rate_limit(response)
        if response.status_code >= 400:
            raise ProviderError(f"pubmed esearch returned {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("pubmed esearch returned non-JSON") from exc

        return list(payload.get("esearchresult", {}).get("idlist", []))

    async def _efetch(self, pmids: list[str]) -> list[CandidatePaper]:
        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            **self._auth_params(),
        }
        try:
            response = await self._http.get(EFETCH_URL, params=params)
        except httpx.HTTPError as exc:
            raise ProviderError(f"pubmed efetch transport failure: {exc}") from exc

        self._raise_for_rate_limit(response)
        if response.status_code >= 400:
            raise ProviderError(f"pubmed efetch returned {response.status_code}")

        try:
            root = ElementTree.fromstring(response.text)
        except ElementTree.ParseError as exc:
            raise ProviderError(f"pubmed efetch returned unparseable XML: {exc}") from exc

        papers: list[CandidatePaper] = []
        for element in root.findall(".//PubmedArticle"):
            paper = self._parse_article(element)
            if paper is not None:
                papers.append(paper)

        logger.debug("pubmed: %d pmids -> %d usable papers", len(pmids), len(papers))
        return papers

    @staticmethod
    def _raise_for_rate_limit(response: httpx.Response) -> None:
        """E-utilities signals throttling without always using a status code.

        A status-only check silently treats a throttle as an empty result,
        which looks like "no literature exists for this claim" — the single
        most misleading failure this provider can produce.
        """
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            raise RateLimited(
                "pubmed rate limit (429)",
                retry_after=float(retry_after) if retry_after else None,
            )
        if "API rate limit exceeded" in response.text[:500]:
            raise RateLimited("pubmed rate limit (200 body)", retry_after=1.0)

    def _parse_article(self, element: ElementTree.Element) -> CandidatePaper | None:
        """Map one ``<PubmedArticle>`` onto a CandidatePaper.

        Returns None for records with no abstract or no usable identifier —
        both are unusable downstream, so they are dropped at this boundary
        rather than carried forward to fail validation later.
        """
        pmid = _text(element.find(".//PMID"))

        doi = None
        for article_id in element.findall(".//ArticleIdList/ArticleId"):
            if article_id.get("IdType") == "doi":
                doi = (article_id.text or "").strip() or None
                break

        if not pmid and not doi:
            return None

        title = _text(element.find(".//ArticleTitle"))
        if not title:
            return None

        # Structured abstracts arrive as several labelled <AbstractText> parts.
        # Keeping only the first loses the results section, which is the part
        # that actually says what the study found.
        abstract_parts: list[str] = []
        for node in element.findall(".//Abstract/AbstractText"):
            text = "".join(node.itertext()).strip()
            if not text:
                continue
            label = node.get("Label")
            abstract_parts.append(f"{label}: {text}" if label else text)

        abstract = " ".join(abstract_parts).strip()
        if not abstract:
            return None

        journal = _text(element.find(".//Journal/Title"))
        year = _parse_year(element)

        publication_types = [
            "".join(node.itertext()).strip()
            for node in element.findall(".//PublicationTypeList/PublicationType")
        ]

        return CandidatePaper(
            pmid=pmid,
            doi=doi,
            title=title,
            abstract=abstract,
            journal=journal or None,
            year=year,
            study_type=classify_study_type(publication_types, title, abstract),
            # Preserved even when classification fails, so the classifier can be
            # re-run over the cache without re-fetching.
            raw_study_type="; ".join(publication_types) or None,
            citation_count=None,  # PubMed does not expose one
            url=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else None,
            source_api=SourceApi.PUBMED,
        )


def _text(element: ElementTree.Element | None) -> str:
    return "".join(element.itertext()).strip() if element is not None else ""


def _parse_year(element: ElementTree.Element) -> int | None:
    """Publication year, tolerating PubMed's several date encodings."""
    for path in (
        ".//JournalIssue/PubDate/Year",
        ".//ArticleDate/Year",
        ".//PubMedPubDate[@PubStatus='pubmed']/Year",
    ):
        value = _text(element.find(path))
        if value[:4].isdigit():
            return int(value[:4])

    # Some records carry only a MedlineDate like "2019 Nov-Dec".
    medline = _text(element.find(".//JournalIssue/PubDate/MedlineDate"))
    if medline[:4].isdigit():
        return int(medline[:4])
    return None
