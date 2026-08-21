"""The sources cache — pgvector as a persistent library, not a search index.

Once a paper is fetched and embedded it is kept forever and reused by every
future article. The tenth ashwagandha article costs almost no embedding calls.
This is the whole reason the vector store exists; searching it is secondary.

Ordering rule, and it matters: **upsert first, embed second.** Upsert reports
which rows already carry a current vector, so only genuinely new abstracts are
sent to the embedding provider. Embedding first and then upserting works, and
quietly pays to re-embed the entire candidate set on every run.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Integer, Text, column, func, or_, select, update, values
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.contracts import CandidatePaper
from app.domain.models import Source
from app.llm.embeddings.base import EmbeddingProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CachedSource:
    source_id: str
    paper: CandidatePaper
    had_embedding: bool


@dataclass(frozen=True, slots=True)
class _ExistingRow:
    """A ``sources`` row already in the cache, matched by one of its ids."""

    id: str
    pmid: str | None
    doi: str | None
    embedding_model: str | None


@dataclass(frozen=True, slots=True)
class _Match:
    """The cached row an incoming paper resolves to."""

    row: _ExistingRow
    #: True when the paper's *other* identifier belongs to a different row —
    #: the cache is holding one paper twice. The row is still usable; what it
    #: cannot do is adopt the contested identifier.
    split: bool


class SourceCache:
    def __init__(self, session: AsyncSession, embedder: EmbeddingProvider) -> None:
        self._session = session
        self._embedder = embedder

    async def upsert_many(self, papers: list[CandidatePaper]) -> list[CachedSource]:
        """Insert or refresh candidates, returning their ids and cache state.

        **Resolve first, then write.** A paper's identity spans two partial
        unique indexes — ``lower(doi)`` and ``pmid`` — and Postgres accepts one
        inference clause per statement, so no single ``ON CONFLICT`` can see
        both. Routing by whichever identifier the incoming record happens to
        carry is what the previous two-pass version did, and it breaks the
        moment a record arrives *more complete* than the row it matches: a
        paper first seen PMID-only, met again later carrying a DOI, takes the
        DOI pass, infers against an index with no matching entry, attempts an
        INSERT and violates ``sources_pmid_key``. That is an IntegrityError
        that kills the run, and cross-provider merging
        (``retrieval/dedup.py``) makes newly-complete records the normal case
        rather than a rare one.

        So existing rows are read once, matched on *every* identifier at once,
        and updated by primary key — against which no conflict target exists.
        Only genuinely new papers are inserted.

        This is a read-then-write, which DESIGN.md §9 originally ruled out. The
        race it opens is two workers inserting the same new paper between our
        read and our write; it surfaces as an IntegrityError and is retried
        once, by which point the row exists and the retry resolves it as an
        ordinary match.

        On refresh a row is never replaced: citation count, last-seen and any
        newly-available identifier are updated, while ``embedding`` and
        ``abstract`` are left alone. Overwriting the embedding would discard
        the cache benefit this class exists to provide.

        Callers may pass the same paper twice — candidates are collected per
        claim, and one paper routinely answers several claims — so the batch is
        collapsed on ``dedup_key`` rather than trusting every caller.
        """
        if not papers:
            return []

        by_dedup_key: dict[str, CandidatePaper] = {}
        for paper in papers:
            by_dedup_key.setdefault(paper.dedup_key, paper)
        papers = list(by_dedup_key.values())

        results: list[CachedSource] = []
        for attempt in range(2):
            try:
                # A savepoint, not the outer transaction: a retry must discard
                # only this batch, and the stage's transaction already holds
                # work that must survive it. (The orchestrator commits per
                # stage, so "the outer transaction" is one stage's, not the
                # whole run's — the savepoint is needed either way.)
                async with self._session.begin_nested():
                    results = await self._write(papers)
                break
            except IntegrityError:
                if attempt:
                    raise
                logger.warning("source cache: concurrent insert; retrying once")

        cached = sum(1 for entry in results if entry.had_embedding)
        logger.info(
            "source cache: %d papers -> %d rows (%d already embedded)",
            len(papers),
            len(results),
            cached,
        )
        return results

    async def _write(self, papers: list[CandidatePaper]) -> list[CachedSource]:
        """Match against what is already cached, then refresh or insert."""
        existing = await self._load_existing(papers)

        results: list[CachedSource] = []
        new_papers: list[CandidatePaper] = []
        # Split by whether the row may adopt the paper's other identifier: the
        # two groups need different SET clauses, so they are two statements
        # rather than N.
        adopting: list[tuple[_ExistingRow, CandidatePaper]] = []
        contested: list[tuple[_ExistingRow, CandidatePaper]] = []

        for paper in papers:
            match = _match(paper, existing)
            if match is None:
                new_papers.append(paper)
                continue
            (contested if match.split else adopting).append((match.row, paper))
            results.append(
                CachedSource(
                    source_id=match.row.id,
                    paper=paper,
                    # A vector from a different model occupies a different
                    # space, so it is not reusable — treat it as absent.
                    had_embedding=(
                        match.row.embedding_model == self._embedder.model_id
                    ),
                )
            )

        await self._refresh_many(adopting, adopt_identifiers=True)
        await self._refresh_many(contested, adopt_identifiers=False)
        results.extend(await self._insert(new_papers))
        return results

    async def _load_existing(
        self, papers: list[CandidatePaper]
    ) -> dict[str, _ExistingRow]:
        """Every cached row matching any identifier in the batch, keyed by id.

        One query covering both partial unique indexes. Indexed on either side,
        and a batch is a few hundred identifiers at most.
        """
        dois = [paper.doi.strip().lower() for paper in papers if paper.doi]
        pmids = [paper.pmid.strip() for paper in papers if paper.pmid]

        conditions = []
        if dois:
            conditions.append(func.lower(Source.doi).in_(dois))
        if pmids:
            conditions.append(Source.pmid.in_(pmids))
        if not conditions:
            return {}

        result = await self._session.execute(
            select(Source.id, Source.pmid, Source.doi, Source.embedding_model).where(
                or_(*conditions)
            )
        )

        index: dict[str, _ExistingRow] = {}
        for row in result.all():
            entry = _ExistingRow(
                id=str(row.id),
                pmid=row.pmid,
                doi=row.doi,
                embedding_model=row.embedding_model,
            )
            if entry.doi:
                index[f"doi:{entry.doi.strip().lower()}"] = entry
            if entry.pmid:
                index[f"pmid:{entry.pmid.strip()}"] = entry
        return index

    async def _refresh_many(
        self,
        matched: list[tuple[_ExistingRow, CandidatePaper]],
        *,
        adopt_identifiers: bool,
    ) -> None:
        """Refresh every matched row in **one** statement, joined to a VALUES list.

        One UPDATE per row is the obvious way to write this and it is a hundred
        round trips on a warm cache — which is the common case, since the cache
        exists precisely so a repeat topic re-matches everything. Joining
        against a VALUES list does the same work in one.

        Identifiers COALESCE in both directions: adopt one we did not have, and
        keep one the incoming record lacks. The same paper arrives from
        different providers with different identifier subsets, so merging beats
        overwriting.

        ``adopt_identifiers=False`` for rows where the paper's other identifier
        belongs to a *different* row (see ``_match``). Adopting it would write a
        value another row already owns and the partial unique index would reject
        the statement — turning a duplicate that merely needs cleaning up into a
        failed run. Those rows are still touched, so ``last_seen_at`` and the
        citation count stay current while the split is reported. They are a
        separate statement rather than a separate SET expression because the
        difference is which columns are written at all.
        """
        if not matched:
            return

        incoming = values(
            column("id", UUID(as_uuid=False)),
            column("pmid", Text),
            column("doi", Text),
            column("citation_count", Integer),
            name="incoming",
        ).data(
            [
                (row.id, paper.pmid, paper.doi, paper.citation_count)
                for row, paper in matched
            ]
        )

        assignments: dict[str, Any] = {
            "last_seen_at": datetime.now(UTC),
            # The cast is load-bearing, not decoration. Postgres types a VALUES
            # column from its literals, and a batch where no paper carries a
            # citation count is a column of bare NULLs — inferred as `text`,
            # which then fails to COALESCE with an integer column. The text
            # columns below are immune for the same reason: their fallback type
            # is already the right one.
            "citation_count": func.coalesce(
                incoming.c.citation_count.cast(Integer), Source.citation_count
            ),
        }
        if adopt_identifiers:
            assignments["pmid"] = func.coalesce(Source.pmid, incoming.c.pmid)
            assignments["doi"] = func.coalesce(Source.doi, incoming.c.doi)

        await self._session.execute(
            update(Source).where(Source.id == incoming.c.id).values(**assignments)
        )

    async def _insert(self, papers: list[CandidatePaper]) -> list[CachedSource]:
        """Insert papers that matched nothing. A new row never has a vector."""
        if not papers:
            return []

        now = datetime.now(UTC)
        result = await self._session.execute(
            Source.__table__.insert()
            .values(
                [
                    {
                        "pmid": paper.pmid,
                        "doi": paper.doi,
                        "title": paper.title,
                        "abstract": paper.abstract,
                        "journal": paper.journal,
                        "year": paper.year,
                        "study_type": paper.study_type,
                        "raw_study_type": paper.raw_study_type,
                        "citation_count": paper.citation_count,
                        "url": paper.url,
                        "source_api": paper.source_api.value,
                        "created_at": now,
                        "last_seen_at": now,
                    }
                    for paper in papers
                ]
            )
            .returning(Source.id)
        )

        # RETURNING preserves the order of the VALUES list for a plain INSERT.
        return [
            CachedSource(source_id=str(row.id), paper=paper, had_embedding=False)
            for row, paper in zip(result.all(), papers, strict=True)
        ]

    async def ensure_embeddings(self, cached: list[CachedSource]) -> None:
        """Embed and store vectors for any source lacking a current one.

        Embeds the abstract alone — not title + abstract concatenated. Titles
        carry marketing-adjacent phrasing that pulls the vector toward the
        claim's wording rather than the study's findings, which is exactly the
        similarity we don't want to reward.
        """
        pending = [entry for entry in cached if not entry.had_embedding]
        if not pending:
            return

        vectors = await self._embedder.embed_documents(
            [entry.paper.abstract for entry in pending]
        )

        for entry, vector in zip(pending, vectors, strict=True):
            await self._session.execute(
                update(Source)
                .where(Source.id == entry.source_id)
                .values(embedding=vector, embedding_model=self._embedder.model_id)
            )

        logger.info(
            "embedded %d new abstracts (%s)", len(pending), self._embedder.model_id
        )


def _match(paper: CandidatePaper, existing: dict[str, _ExistingRow]) -> _Match | None:
    """The cached row this paper already has, if any.

    Looks up *both* identifiers. Each index is unique, so a paper matches at
    most one row per identifier — and when it matches two *different* rows, the
    cache is holding one paper twice, split under two identifier subsets by the
    keying this module used to do.

    Splits are reported, not repaired. Repair means re-pointing
    ``article_sources`` and deleting a row that ``ON DELETE RESTRICT`` exists
    to protect, so doing it here would let a cache refresh rewrite the
    provenance of already-published articles as a side effect of fetching
    abstracts. That is a maintenance job with its own transaction.

    The DOI row wins, DOI being the more portable identifier and the one the
    next run resolves to as well — so the choice is stable rather than
    arbitrary, and the same article keeps citing the same source id.
    """
    by_doi = existing.get(f"doi:{paper.doi.strip().lower()}") if paper.doi else None
    by_pmid = existing.get(f"pmid:{paper.pmid.strip()}") if paper.pmid else None

    split = by_doi is not None and by_pmid is not None and by_doi.id != by_pmid.id
    if split:
        assert by_doi is not None and by_pmid is not None
        logger.warning(
            "sources %s (doi=%s) and %s (pmid=%s) are the same paper held twice; "
            "using the DOI row. The duplicate needs merging out of band.",
            by_doi.id,
            by_doi.doi,
            by_pmid.id,
            by_pmid.pmid,
        )

    row = by_doi or by_pmid
    return None if row is None else _Match(row=row, split=split)
