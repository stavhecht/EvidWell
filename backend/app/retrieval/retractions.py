"""Asking the literature whether a paper it published still stands.

This is the half of retraction handling that actually earns its place. Refusing
a retracted paper at ingest (``evidence/grading.py::is_retracted``) only works
for papers that were already retracted when we first saw them — and retractions
land *after* publication, typically by months or years. A source cached clean in
August and retracted in November stays cited by a live article, and nothing in
the pipeline ever looks at it again.

**Two providers, because neither alone covers the corpus.** PubMed's
``esummary`` carries a ``pubtype`` array with "Retracted Publication" on the
paper itself; it batches 200 ids per request and is the cheap path, but it only
knows papers it indexes and two of the cached rows have no PMID at all. Crossref
carries an ``update-to`` relation naming the retraction, covers every paper with
a DOI, and costs one request each. Measured against the live corpus on
2026-08-21: PubMed resolved 90/90 PMIDs, Crossref 92/92 DOIs.

**An unchecked paper is not a clean paper**, and keeping those apart is the
whole reliability story here. This is the same failure the throttle exists to
prevent one layer down (``retrieval/throttle.py``): a provider that fails to
answer produces silence, silence looks like "nothing wrong", and the result is a
retracted citation marked verified. So ``RetractionVerdict`` has three states,
not two, and ``sources.retraction_checked_at`` is written only when a provider
actually answered. A sweep that reaches nothing must report zero checked, never
zero retracted.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from app.evidence.grading import CONCERN_PUBLICATION_TYPES, RETRACTION_PUBLICATION_TYPES
from app.retrieval.throttle import HttpClient

logger = logging.getLogger(__name__)

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
CROSSREF_BASE = "https://api.crossref.org/works"

#: ``esummary`` accepts far more, but a URL of 200 PMIDs is already ~1.8kB and
#: NCBI recommends POST past that. 200 keeps every request a GET while turning
#: a thousand-paper sweep into five calls.
PUBMED_BATCH_SIZE = 200

#: Crossref ``update-to`` relation types that mean withdrawn. ``corrected`` and
#: ``addendum`` also appear in that array and deliberately do not qualify: a
#: correction is the literature working, not failing.
CROSSREF_RETRACTION_TYPES = frozenset({"retraction", "withdrawal", "removal"})
CROSSREF_CONCERN_TYPES = frozenset({"expression_of_concern", "expression-of-concern"})


@dataclass(frozen=True, slots=True)
class PaperIdentity:
    """What the checker needs to look one cached row up, and nothing else."""

    source_id: str
    pmid: str | None = None
    doi: str | None = None


@dataclass(frozen=True, slots=True)
class RetractionVerdict:
    """Three states, not two.

    ``checked`` is separate from ``retracted`` because "we asked and it is
    fine" and "we could not ask" are the same absence of a positive result and
    opposite facts about the world. Collapsing them writes a
    ``retraction_checked_at`` for a paper nobody checked, which then suppresses
    the *next* sweep too — the error compounds rather than being retried.
    """

    #: A provider answered. False means every provider failed or knew nothing.
    checked: bool = False
    #: The literature has withdrawn this paper.
    retracted: bool = False
    #: Under investigation, not withdrawn. Recorded, not refused.
    concern: bool = False
    #: Human-readable, persisted to ``sources.retraction_note``.
    detail: str = ""
    #: Which providers answered. Empty when nothing did.
    answered_by: tuple[str, ...] = field(default=())

    def merge(self, other: RetractionVerdict) -> RetractionVerdict:
        """Combine two providers' answers about one paper.

        A positive from either wins. Two independent bibliographic databases
        disagreeing about a retraction is not a tie to be broken by precedence
        — one of them has newer data, and it is always the one saying yes.
        """
        details = [d for d in (self.detail, other.detail) if d]
        return RetractionVerdict(
            checked=self.checked or other.checked,
            retracted=self.retracted or other.retracted,
            concern=self.concern or other.concern,
            detail="; ".join(details),
            answered_by=self.answered_by + other.answered_by,
        )


UNCHECKED = RetractionVerdict()


class RetractionSource(Protocol):
    """One bibliographic record that can be asked about a paper's status.

    Implementations must **omit** a paper they could not resolve rather than
    returning a clean verdict for it. An absent key means "no answer" and lets
    another source, or the next sweep, try again; a `checked=True, retracted=
    False` verdict for a paper nobody looked at is the bug this whole module is
    shaped around.
    """

    name: str

    async def check(
        self, papers: Sequence[PaperIdentity]
    ) -> dict[str, RetractionVerdict]:
        """Verdicts keyed by ``source_id``, for the papers this source knows."""
        ...


class PubMedRetractionSource:
    """``esummary.pubtype``, batched.

    Reuses the publication-type vocabulary from ``evidence/grading.py`` rather
    than restating it. The ingest-time refusal and this sweep must agree on
    what a retraction is, or a paper refused at retrieval would be marked clean
    here — and the two checks would be quietly enforcing different rules.
    """

    name = "pubmed"

    def __init__(self, http: HttpClient, api_key: str | None = None) -> None:
        self._http = http
        self._api_key = api_key

    async def check(
        self, papers: Sequence[PaperIdentity]
    ) -> dict[str, RetractionVerdict]:
        with_pmid = [p for p in papers if p.pmid]
        if not with_pmid:
            return {}

        verdicts: dict[str, RetractionVerdict] = {}
        for start in range(0, len(with_pmid), PUBMED_BATCH_SIZE):
            batch = with_pmid[start : start + PUBMED_BATCH_SIZE]
            params: dict[str, str] = {
                "db": "pubmed",
                "id": ",".join(str(p.pmid) for p in batch),
                "retmode": "json",
            }
            if self._api_key:
                params["api_key"] = self._api_key

            try:
                response = await self._http.get(
                    f"{EUTILS_BASE}/esummary.fcgi", params=params
                )
                response.raise_for_status()
                result = response.json().get("result", {})
            except Exception as exc:
                # Deliberately broad, and deliberately not fatal: a transport
                # error, a 500 and a schema change all mean the same thing here
                # — we did not find out. The batch is left *unchecked* rather
                # than marked clean, so the next sweep retries it. Narrowing
                # this to RateLimited would let an unanticipated failure escape
                # and abort a sweep that had already verified thousands.
                logger.warning(
                    "pubmed retraction check failed for %d papers: %s", len(batch), exc
                )
                continue

            for paper in batch:
                record = result.get(str(paper.pmid))
                if record is None or record.get("error"):
                    # PubMed answered, and the answer is about the *id*, not
                    # about the paper's status — so this stays unchecked and
                    # Crossref gets its turn.
                    #
                    # The `error` branch is not defensive padding. NCBI does
                    # not omit an unresolvable id; it returns a record for it
                    # carrying `{"error": "cannot get document summary"}` and
                    # no `pubtype`. Reading that as an empty type list marks a
                    # paper nobody could look up as verified clean, and then
                    # writes a `retraction_checked_at` that suppresses the next
                    # sweep — the one failure mode this module is built around,
                    # arriving through the provider that was supposed to be the
                    # reliable one. Caught by a deliberately unresolvable
                    # control, not by review.
                    continue
                verdicts[paper.source_id] = _from_publication_types(
                    record.get("pubtype", []), self.name
                )

        return verdicts


def _from_publication_types(types: Sequence[str], provider: str) -> RetractionVerdict:
    normalised = {str(t).strip().lower() for t in types}
    retracted = bool(normalised & RETRACTION_PUBLICATION_TYPES)
    concern = bool(normalised & CONCERN_PUBLICATION_TYPES)
    detail = ""
    if retracted or concern:
        flagged = sorted(
            normalised & (RETRACTION_PUBLICATION_TYPES | CONCERN_PUBLICATION_TYPES)
        )
        detail = f"{provider}: {', '.join(flagged)}"
    return RetractionVerdict(
        checked=True,
        retracted=retracted,
        concern=concern,
        detail=detail,
        answered_by=(provider,),
    )


class CrossrefRetractionSource:
    """``update-to``, one request per DOI.

    Slower than PubMed per paper and worth it for two reasons: it covers rows
    with no PMID, and it is a genuinely independent record. A journal that
    files a retraction with Crossref before NCBI reindexes is the normal case,
    not the edge one, and the sweep is looking for exactly that lag.
    """

    name = "crossref"

    def __init__(self, http: HttpClient, mailto: str | None = None) -> None:
        self._http = http
        self._mailto = mailto

    async def check(
        self, papers: Sequence[PaperIdentity]
    ) -> dict[str, RetractionVerdict]:
        verdicts: dict[str, RetractionVerdict] = {}
        for paper in papers:
            if not paper.doi:
                continue
            verdict = await self._check_one(paper.doi)
            if verdict.checked:
                verdicts[paper.source_id] = verdict
        return verdicts

    async def _check_one(self, doi: str) -> RetractionVerdict:
        params = {"mailto": self._mailto} if self._mailto else None
        try:
            response = await self._http.get(f"{CROSSREF_BASE}/{doi}", params=params)
            if response.status_code == 404:
                # Crossref answered: it has no record under this DOI. Same
                # reasoning as the PubMed miss — an answer about the id, not
                # about the paper.
                return UNCHECKED
            response.raise_for_status()
            message = response.json()["message"]
        except Exception as exc:
            # Same contract as the PubMed batch above: unchecked, not clean.
            logger.warning("crossref retraction check failed for %s: %s", doi, exc)
            return UNCHECKED

        updates = message.get("update-to") or []
        kinds = {str(u.get("type") or "").strip().lower() for u in updates}
        retracted = bool(kinds & CROSSREF_RETRACTION_TYPES)
        concern = bool(kinds & CROSSREF_CONCERN_TYPES)
        detail = ""
        if retracted or concern:
            flagged = sorted(
                kinds & (CROSSREF_RETRACTION_TYPES | CROSSREF_CONCERN_TYPES)
            )
            detail = f"{self.name}: {', '.join(flagged)}"
        return RetractionVerdict(
            checked=True,
            retracted=retracted,
            concern=concern,
            detail=detail,
            answered_by=(self.name,),
        )


class RetractionChecker:
    """Fans one set of papers out across every configured source and merges.

    Sources run **sequentially**, not concurrently. Each is already paced by its
    own ``ThrottledClient``, and running them together would double the
    wall-clock saving while doubling the chance of tripping a rate limit on a
    job that has no deadline — a sweep that takes four minutes instead of two is
    not a problem worth a 429 for.
    """

    def __init__(self, sources: Sequence[RetractionSource]) -> None:
        if not sources:
            raise ValueError("a retraction checker with no sources checks nothing")
        self._sources = list(sources)

    async def check(
        self, papers: Sequence[PaperIdentity]
    ) -> dict[str, RetractionVerdict]:
        """Verdict per ``source_id``, including papers nothing could answer for.

        Every input paper appears in the result. A caller that only saw the
        answered ones would have to reconstruct the difference to know what it
        failed to check, and the whole point is that the gap stays visible.
        """
        merged: dict[str, RetractionVerdict] = {p.source_id: UNCHECKED for p in papers}
        for source in self._sources:
            try:
                answers = await source.check(papers)
            except Exception:
                logger.exception("retraction source %s failed entirely", source.name)
                continue
            for source_id, verdict in answers.items():
                merged[source_id] = merged[source_id].merge(verdict)
        return merged
