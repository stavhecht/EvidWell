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

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
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


class SourceCache:
    def __init__(self, session: AsyncSession, embedder: EmbeddingProvider) -> None:
        self._session = session
        self._embedder = embedder

    async def upsert_many(self, papers: list[CandidatePaper]) -> list[CachedSource]:
        """Insert or refresh candidates, returning their ids and cache state.

        Two passes, because there are two conflict targets (the partial unique
        indexes on ``lower(doi)`` and on ``pmid``) and Postgres accepts one
        inference clause per statement. DOI-bearing rows go first so a paper
        carrying both identifiers is keyed on the more portable one.

        On conflict the row is *refreshed*, never replaced: citation count,
        last-seen, and any newly-available identifier are updated, but
        ``embedding`` and ``abstract`` are left alone. Overwriting the embedding
        would discard the cache benefit this class exists to provide.
        """
        if not papers:
            return []

        with_doi = [paper for paper in papers if paper.doi]
        without_doi = [paper for paper in papers if not paper.doi and paper.pmid]

        results: list[CachedSource] = []
        results.extend(await self._upsert_pass(with_doi, conflict="doi"))
        results.extend(await self._upsert_pass(without_doi, conflict="pmid"))

        cached = sum(1 for entry in results if entry.had_embedding)
        logger.info(
            "source cache: %d papers -> %d rows (%d already embedded)",
            len(papers),
            len(results),
            cached,
        )
        return results

    async def _upsert_pass(
        self, papers: list[CandidatePaper], *, conflict: str
    ) -> list[CachedSource]:
        if not papers:
            return []

        now = datetime.now(UTC)
        rows = [
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

        statement = insert(Source).values(rows)
        index_elements = (
            [Source.doi] if conflict == "doi" else [Source.pmid]
        )
        index_where = (
            Source.doi.isnot(None) if conflict == "doi" else Source.pmid.isnot(None)
        )

        statement = statement.on_conflict_do_update(
            index_elements=index_elements,
            index_where=index_where,
            set_={
                "last_seen_at": statement.excluded.last_seen_at,
                # COALESCE both directions: keep an identifier we already knew
                # when the incoming record lacks it, and adopt one we didn't
                # have. The same paper arrives from different providers with
                # different identifier subsets, so merging beats overwriting.
                "pmid": func.coalesce(statement.excluded.pmid, Source.pmid),
                "doi": func.coalesce(statement.excluded.doi, Source.doi),
                "citation_count": func.coalesce(
                    statement.excluded.citation_count, Source.citation_count
                ),
            },
        ).returning(Source.id, Source.pmid, Source.doi, Source.embedding_model)

        result = await self._session.execute(statement)
        db_rows = result.all()

        # Map returned rows back to the input papers by identifier.
        by_key: dict[str, CandidatePaper] = {}
        for paper in papers:
            if paper.doi:
                by_key[f"doi:{paper.doi.lower()}"] = paper
            if paper.pmid:
                by_key[f"pmid:{paper.pmid}"] = paper

        cached: list[CachedSource] = []
        for row in db_rows:
            paper = None
            if row.doi:
                paper = by_key.get(f"doi:{row.doi.lower()}")
            if paper is None and row.pmid:
                paper = by_key.get(f"pmid:{row.pmid}")
            if paper is None:
                continue
            cached.append(
                CachedSource(
                    source_id=str(row.id),
                    paper=paper,
                    # A vector from a different model occupies a different
                    # space, so it is not reusable — treat it as absent.
                    had_embedding=row.embedding_model == self._embedder.model_id,
                )
            )
        return cached

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

        logger.info("embedded %d new abstracts (%s)", len(pending), self._embedder.model_id)

    async def touch(self, source_ids: list[str]) -> None:
        """Bump ``last_seen_at``. Supports a later staleness sweep."""
        if not source_ids:
            return
        await self._session.execute(
            update(Source)
            .where(Source.id.in_(source_ids))
            .values(last_seen_at=datetime.now(UTC))
        )

    async def load(self, source_ids: list[str]) -> dict[str, Source]:
        """Fetch cached rows by id, keyed for lookup."""
        if not source_ids:
            return {}
        result = await self._session.execute(
            select(Source).where(Source.id.in_(source_ids))
        )
        return {row.id: row for row in result.scalars()}
