"""Full text of open-access papers, from Europe PMC.

Two read-only calls:

1. ``open_access_pmcids`` asks which papers have an open-access full text: by
   PMID, or by DOI for a paper that has none. One request covers every source
   of an article (``retrieval/identifiers.py``).
2. ``sections`` downloads one paper (JATS XML) and returns its body as
   (section title, text) pairs, in reading order.

Only the article body is read, and only the parts that report the paper's own
work. References, acknowledgements and funding live in the back matter and are
never returned. Introduction and background sections are skipped because they
summarise other papers. Tables and figures are skipped because their text
arrives as a jumble of cells that would read as prose.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Protocol

from app.retrieval.identifiers import EUROPE_PMC_REST, EuropePMCIdentifiers, europe_pmc_get
from app.retrieval.throttle import HttpClient

#: Section titles that are not evidence, for papers that put them in the body.
#: Matched anywhere in the lower-cased title. Not a bare "availability", which
#: would also drop a section on a supplement's bioavailability.
_SKIPPED_SECTIONS = (
    "acknowledg",
    "funding",
    "conflict",
    "competing interest",
    "contributions",
    "abbreviation",
    "supplementary",
    "data availability",
    "availability of data",
    "declaration",
    "ethics approval",
    "consent for publication",
)

#: Sections that summarise *other* papers' findings. They echo a claim's wording
#: closely, so they would win on similarity — and an excerpt from one invites
#: the model to credit this paper with results from its reference list.
_OTHER_PAPERS_SECTIONS = ("introduction", "background")

#: Elements whose text is not prose.
_SKIPPED_ELEMENTS = frozenset({"table-wrap", "fig", "disp-formula", "supplementary-material"})


@dataclass(frozen=True, slots=True)
class Section:
    title: str | None
    text: str


class FullTextClient(Protocol):
    async def open_access_pmcids(
        self, pmids: list[str], dois: list[str]
    ) -> dict[str, str]:
        """PMCID for every paper that has an open-access full text, keyed
        ``pmid:<pmid>`` or ``doi:<lower-cased doi>`` by the identifier asked."""
        ...

    async def sections(self, pmcid: str) -> list[Section]:
        """The paper's body sections, in reading order."""
        ...


class EuropePMCFullText:
    def __init__(self, http: HttpClient) -> None:
        self._http = http
        self._ids = EuropePMCIdentifiers(http)

    async def open_access_pmcids(
        self, pmids: list[str], dois: list[str]
    ) -> dict[str, str]:
        wanted_pmids, wanted_dois = set(pmids), {doi.lower() for doi in dois}
        found: dict[str, str] = {}
        for record in await self._ids.records(pmids=wanted_pmids, dois=wanted_dois):
            if not (record.open_access and record.pmcid):
                continue
            if record.pmid in wanted_pmids:
                found[f"pmid:{record.pmid}"] = record.pmcid
            if record.doi in wanted_dois:
                found[f"doi:{record.doi}"] = record.pmcid
        return found

    async def sections(self, pmcid: str) -> list[Section]:
        url = f"{EUROPE_PMC_REST}/{pmcid}/fullTextXML"
        response = await europe_pmc_get(self._http, url, None)
        return parse_sections(response.text)


def parse_sections(xml: str) -> list[Section]:
    """The body of a JATS article as (section title, text), in reading order.

    Each top-level section becomes one ``Section``; its sub-sections' paragraphs
    are folded into it. A paragraph sitting directly in the body, outside any
    section, becomes an untitled ``Section`` of its own.
    """
    body = ET.fromstring(xml).find("body")
    if body is None:
        return []

    sections: list[Section] = []
    for child in body:
        if child.tag == "p":
            title, text = None, _text(child)
        elif child.tag == "sec":
            title = _text(child.find("title")) or None
            skipped = _SKIPPED_SECTIONS + _OTHER_PAPERS_SECTIONS
            if title and any(word in title.lower() for word in skipped):
                continue
            text = "\n\n".join(_paragraphs(child))
        else:
            continue
        if text:
            sections.append(Section(title=title, text=text))
    return sections


def _paragraphs(element: ET.Element) -> list[str]:
    """Every paragraph under ``element``, skipping tables, figures and formulas."""
    found: list[str] = []
    for child in element:
        if child.tag in _SKIPPED_ELEMENTS:
            continue
        if child.tag == "p":
            if text := _text(child):
                found.append(text)
        else:
            found.extend(_paragraphs(child))
    return found


def _text(element: ET.Element | None) -> str:
    """All text inside ``element``, with whitespace collapsed and the paper's
    own reference markers removed."""
    if element is None:
        return ""
    return _REFERENCE_RE.sub("", " ".join("".join(element.itertext()).split()))


#: A numbered reference in square brackets: "[34]", "[34,35,36]", "[39-41]".
#: It points into a reference list the model never sees, so it tells the model
#: nothing — and a square bracket that is not a citation handle fails the draft
#: if the model copies it into the article. Measured 2026-09-29: a Results
#: excerpt carried "[34,35,36,37,38,40,42]".
_REFERENCE_RE = re.compile(r"\s*\[\s*\d+(?:\s*[,\u2013-]\s*\d+)*\s*\]")
