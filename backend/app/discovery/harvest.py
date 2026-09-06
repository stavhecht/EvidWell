"""The only network code in this package: PubMed's index, read for MeSH tags.

Deliberately not a ``ScholarlyProvider`` and deliberately not reusing
``retrieval/pubmed.py``'s parser, for three reasons that are each independently
sufficient:

1. **The contracts conflict.** ``ScholarlyProvider.search`` requires
   implementations to drop records with an empty abstract, and
   ``_parse_article`` does. Discovery wants exactly those records — a MEDLINE
   record with no abstract still carries its full MeSH heading list, which is
   the entire signal here. Measured against the live index on 2026-09-04:
   21,689 abstract-less but indexed records in the supplements and vitamins nets
   alone.
2. **``CandidatePaper`` is the grounding currency.** A ``mesh`` field on it would
   be ``None`` for three of the four providers and read by none of the seven
   stages.
3. **The shared load-bearing thing is the throttle, not the parser** — and
   ``scripts/check_retractions.py`` already establishes exactly this shape: its
   own client, the factory's ``throttled_client``, its own PubMed calls.

What *is* shared is the pacing, and it must be: NCBI's ceiling is per IP and
this process shares that IP with the pipeline worker. Construct this with a
``ThrottledClient`` from ``retrieval/factory.py``, never a raw httpx client.

The esearch parameters differ from the pipeline's in three ways that the
pipeline must not inherit, which is the other half of why this file exists:
``datetype=edat`` (indexing date, not publication date), day-granularity
``mindate``/``maxdate``, and ``sort=date``. The pipeline wants the best 25 papers
for a claim; this wants every paper indexed in a fortnight.
"""

from __future__ import annotations

import logging
from datetime import date
from xml.etree import ElementTree

import httpx

from app.discovery.contracts import DescriptorHit, MeshRecord
from app.discovery.seeds import SeedQuery
from app.discovery.vocab import INTERVENTION_QUALIFIER_UIS
from app.evidence.grading import classify_study_type
from app.retrieval.base import ProviderError, RateLimited
from app.retrieval.pubmed import EFETCH_URL, ESEARCH_URL, detect_throttle
from app.retrieval.throttle import HttpClient, retry_after_seconds

logger = logging.getLogger(__name__)

#: PMIDs per efetch. NCBI's documented ceiling for a GET is 200; above that the
#: request must become a POST, which is a different code path for no benefit at
#: this volume.
EFETCH_BATCH_SIZE = 200

#: PMIDs per esearch page. esearch caps ``retmax`` at 10,000 in any case, and a
#: smaller page keeps a stalled sweep's blast radius small.
ESEARCH_PAGE_SIZE = 500


def _format_date(value: date) -> str:
    """PubMed's date format. Day granularity — the whole point of this module."""
    return value.strftime("%Y/%m/%d")


class PubMedIndexHarvester:
    """Reads newly-indexed records and their MeSH tags.

    Args:
        http: A throttled client. See the module docstring — passing a raw
            httpx client works and is how an IP gets blocked.
        api_key: An NCBI key. Optional, and strongly recommended for a sweep:
            it raises the ceiling from 3 to 10 requests per second, and it makes
            the traffic attributable, so NCBI emails before blocking.
    """

    def __init__(self, http: HttpClient, api_key: str | None = None) -> None:
        self._http = http
        self._api_key = api_key

    def _auth_params(self) -> dict[str, str]:
        return {"api_key": self._api_key} if self._api_key else {}

    async def count_and_ids(
        self,
        seed: SeedQuery,
        start: date,
        end: date,
        *,
        retstart: int = 0,
        retmax: int = ESEARCH_PAGE_SIZE,
    ) -> tuple[int, list[str]]:
        """One page of PMIDs indexed in ``[start, end]``, and the total matching.

        The total is returned alongside so the caller can page without a second
        request and can tell "this seed matched 40 records" from "this seed
        matched 40,000 and we took the first page" — the second is a request
        budget being spent, and the caller enforces the ceiling.
        """
        params = {
            "db": "pubmed",
            "term": seed.esearch_term(),
            "retstart": str(retstart),
            "retmax": str(retmax),
            "retmode": "json",
            # Not `pdat`. The journal's publication date can precede or follow
            # PubMed entry by months, so a fortnight measured on it is not a
            # fortnight of newly-visible literature.
            "datetype": "edat",
            "mindate": _format_date(start),
            "maxdate": _format_date(end),
            # Not `relevance`, which is what the pipeline wants and what would
            # make a truncated sweep silently non-representative: relevance to a
            # net this broad is close to meaningless, and the records we would
            # drop at the cap would be the ones the cap exists to sample.
            "sort": "date",
            **self._auth_params(),
        }
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

        result = payload.get("esearchresult", {})
        try:
            total = int(result.get("count", 0))
        except (TypeError, ValueError):
            total = 0
        return total, list(result.get("idlist", []))

    async def fetch_mesh(self, pmids: list[str]) -> list[MeshRecord]:
        """The MeSH profile of each record, in batches.

        Records with no ``<MeshHeadingList>`` are dropped: they are not yet
        indexed, so they carry nothing to count. They are dropped *here* rather
        than filtered in the esearch, because "indexed" is not a search field and
        because a record that gains its MeSH next month will be picked up by the
        overlap — which is exactly the case the window design exists for.
        """
        records: list[MeshRecord] = []
        for start in range(0, len(pmids), EFETCH_BATCH_SIZE):
            batch = pmids[start : start + EFETCH_BATCH_SIZE]
            records.extend(await self._efetch(batch))
        return records

    async def _efetch(self, pmids: list[str]) -> list[MeshRecord]:
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

        records = []
        for element in root.findall(".//PubmedArticle"):
            record = parse_mesh_record(element)
            if record is not None:
                records.append(record)
        logger.debug("efetch: %d pmids -> %d indexed records", len(pmids), len(records))
        return records

    @staticmethod
    def _raise_for_rate_limit(response: httpx.Response) -> None:
        """Same backstop as ``PubMedProvider``, for the same reason.

        NCBI answers overage with a 200 carrying an error string as often as
        with a 429, and reading that body as an empty result set is how a
        throttled sweep reports "no trends this fortnight".
        """
        if response.status_code == 429:
            raise RateLimited(
                "pubmed rate limit (429)", retry_after=retry_after_seconds(response) or None
            )
        if detect_throttle(response) is not None:
            raise RateLimited("pubmed rate limit (200 body)", retry_after=1.0)


def parse_mesh_record(element: ElementTree.Element) -> MeshRecord | None:
    """One ``<PubmedArticle>`` reduced to what discovery counts.

    Returns ``None`` for a record with no PMID, no usable Entrez date, or no
    MeSH headings — all three mean there is nothing to count, and none of them
    is an error.

    Note what is *not* checked: the abstract. This is the difference from
    ``PubMedProvider._parse_article``, and it is the reason this function exists.
    """
    pmid = (element.findtext(".//PMID") or "").strip()
    if not pmid:
        return None

    entrez_date = _parse_entrez_date(element)
    if entrez_date is None:
        return None

    headings = element.findall(".//MeshHeadingList/MeshHeading")
    if not headings:
        return None

    # Chemicals are listed separately from the headings, so the flag has to be
    # collected first and applied by UI. It is the primary evidence that a
    # descriptor names something administered rather than measured.
    chemical_uis = {
        node.get("UI")
        for node in element.findall(".//ChemicalList/Chemical/NameOfSubstance")
        if node.get("UI")
    }

    descriptors: list[DescriptorHit] = []
    for heading in headings:
        descriptor = heading.find("DescriptorName")
        if descriptor is None:
            continue
        ui = descriptor.get("UI")
        name = "".join(descriptor.itertext()).strip()
        if not ui or not name:
            continue
        qualifier_uis = {
            qualifier.get("UI")
            for qualifier in heading.findall("QualifierName")
            if qualifier.get("UI")
        }
        descriptors.append(
            DescriptorHit(
                ui=ui,
                name=name,
                major_topic=descriptor.get("MajorTopicYN") == "Y",
                in_chemical_list=ui in chemical_uis,
                intervention_qualifier=bool(qualifier_uis & INTERVENTION_QUALIFIER_UIS),
            )
        )

    if not descriptors:
        return None

    publication_types = [
        "".join(node.itertext()).strip()
        for node in element.findall(".//PublicationTypeList/PublicationType")
    ]

    return MeshRecord(
        pmid=pmid,
        entrez_date=entrez_date,
        # Publication tags ONLY — no title, no abstract. `classify_study_type`
        # falls back to prose when the tags identify nothing, and that fallback
        # exists for OpenAlex and Semantic Scholar, which supply no publication
        # types at all. PubMed always does, so here the fallback could only ever
        # guess from a headline. The same rule the classifier's own docstring
        # states: abstract heuristics must not run on a paper whose tags already
        # say something. Grading is a scoring weight here rather than an
        # article's evidence grade, so a wrong guess is cheaper than it is in the
        # pipeline — but it is still a guess, and UNKNOWN is the honest answer.
        study_type=classify_study_type(publication_types),
        descriptors=descriptors,
    )


def _parse_entrez_date(element: ElementTree.Element) -> date | None:
    """When PubMed received this record.

    Verified present with Year/Month/Day on every sampled record (2026-09-04),
    but returning ``None`` rather than asserting: a record without one is one
    observation missing, while a raise would fail the whole batch — and the
    overlap will re-read it next scan anyway.
    """
    node = element.find(".//PubMedPubDate[@PubStatus='entrez']")
    if node is None:
        return None
    try:
        return date(
            int(node.findtext("Year") or ""),
            int(node.findtext("Month") or ""),
            int(node.findtext("Day") or ""),
        )
    except ValueError:
        return None
