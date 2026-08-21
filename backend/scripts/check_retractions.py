"""Re-check cited sources against the retraction record, and flag what breaks.

The ingest screen in ``RetrieveStage`` only catches papers that were already
retracted when we first saw them. Retractions land *after* publication, usually
by months or years, so a source cached clean in August and retracted in November
stays cited by a live article and nothing ever looks at it again. This is the
half of the feature that covers that, and it is the half that has to be run on a
schedule to be worth anything.

Dry by default::

    python -m scripts.check_retractions              # report only
    python -m scripts.check_retractions --apply      # write
    python -m scripts.check_retractions --scope all  # not just cited sources

**Scope is cited sources by default**, meaning those referenced by an article
that is published or awaiting review. A few dozen papers, one batched PubMed
call, and it covers every source a reader can currently reach. ``--scope all``
walks the whole cache, which catches a retraction before the paper is ever
cited — worth doing occasionally, and wasteful as the default because most
cached rows support no article.

**Nothing is deleted and nothing is unpublished.** A retracted source has its
row marked, and every article citing it gets ``retraction_flagged_at`` set so it
raises a banner and appears in the console. A human decides what happens next.
Deleting the source would break the provenance of a published article to tidy up
a citation, and withdrawing the article automatically would be the machine
deciding what the public sees — which is the thing invariant #1 exists to
prevent, pointed the other way.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import Counter
from datetime import UTC, datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.db import dispose_engine, get_session_factory
from app.domain.enums import ArticleStatus
from app.domain.models import Article, ArticleSource, Source
from app.retrieval.factory import (
    PUBMED_ANONYMOUS_RPS,
    PUBMED_KEYED_RPS,
    USER_AGENT,
    throttled_client,
)
from app.retrieval.retractions import (
    CrossrefRetractionSource,
    PaperIdentity,
    PubMedRetractionSource,
    RetractionChecker,
    RetractionVerdict,
)

#: Crossref asks for one request per second from the polite pool and is more
#: forgiving than that in practice. Kept at 3 because the sweep has no deadline
#: and being blocked by a bibliographic database is a bad way to find out where
#: the real ceiling is.
CROSSREF_RPS = 3.0

#: Articles a reader can currently reach, or a reviewer is about to approve.
#: `rejected` and `validation_failed` are excluded: nothing points at them, and
#: flagging a rejected draft creates review work with no reader at the end of it.
LIVE_STATUSES = (ArticleStatus.PUBLISHED, ArticleStatus.PENDING_REVIEW)


def build_checker(settings: Settings, http: httpx.AsyncClient) -> RetractionChecker:
    """Both sources, each behind its own throttle.

    Reuses ``retrieval/factory.py``'s pacing rather than opening raw clients,
    because the PubMed ceiling is per IP and shared with the pipeline — a sweep
    running alongside a worker must not be the thing that gets the address
    blocked.
    """
    api_key = settings.pubmed_api_key or None
    return RetractionChecker(
        [
            PubMedRetractionSource(
                throttled_client(
                    settings,
                    http,
                    "pubmed",
                    PUBMED_KEYED_RPS if api_key else PUBMED_ANONYMOUS_RPS,
                ),
                api_key,
            ),
            CrossrefRetractionSource(
                throttled_client(settings, http, "crossref", CROSSREF_RPS),
                # Crossref's polite pool keys on a contact address, and
                # OPENALEX_MAILTO is the one this deployment already has. Both
                # want the same thing — someone to email before blocking you —
                # so a second setting for the same address would be config for
                # its own sake. Rename it if a third API ever wants one.
                settings.openalex_mailto or None,
            ),
        ]
    )


async def _load_papers(
    session: AsyncSession, *, scope: str
) -> list[tuple[Source, list[str]]]:
    """Sources to check, each with the article ids citing it.

    A source with no citing article still gets checked under ``--scope all``;
    it simply has nothing to flag if it turns out to be retracted.
    """
    cited_stmt = (
        select(ArticleSource.source_id, ArticleSource.article_id)
        .join(Article, Article.id == ArticleSource.article_id)
        .where(Article.status.in_(LIVE_STATUSES), ArticleSource.was_cited.is_(True))
    )
    citations: dict[str, list[str]] = {}
    for source_id, article_id in (await session.execute(cited_stmt)).all():
        citations.setdefault(source_id, []).append(article_id)

    stmt = select(Source)
    if scope == "cited":
        if not citations:
            return []
        stmt = stmt.where(Source.id.in_(citations.keys()))
    # Already-known retractions are not re-checked: a retraction is not
    # un-issued, and re-asking wastes the request budget the unchecked rows
    # need. Matches the partial index the migration creates.
    stmt = stmt.where(Source.retracted_at.is_(None)).order_by(
        Source.retraction_checked_at.asc().nullsfirst()
    )

    rows = (await session.execute(stmt)).scalars().all()
    return [(row, citations.get(row.id, [])) for row in rows]


def _summarise(verdicts: dict[str, RetractionVerdict]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for verdict in verdicts.values():
        if not verdict.checked:
            counts["unchecked"] += 1
        elif verdict.retracted:
            counts["retracted"] += 1
        elif verdict.concern:
            counts["concern"] += 1
        else:
            counts["clean"] += 1
    return counts


async def _run() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write; otherwise report")
    parser.add_argument(
        "--scope",
        choices=("cited", "all"),
        default="cited",
        help="cited (default): sources cited by a published or pending article",
    )
    parser.add_argument(
        "--limit", type=int, default=0, help="check at most N sources (0 = no cap)"
    )
    args = parser.parse_args()

    settings = get_settings()
    factory = get_session_factory()

    async with factory() as session:
        papers = await _load_papers(session, scope=args.scope)

    if args.limit:
        papers = papers[: args.limit]
    if not papers:
        print(f"No sources in scope={args.scope!r}. Nothing to check.")
        return 0

    print(f"Checking {len(papers)} source(s), scope={args.scope}…")

    async with httpx.AsyncClient(
        timeout=settings.http_timeout_seconds,
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    ) as http:
        checker = build_checker(settings, http)
        verdicts = await checker.check(
            [
                PaperIdentity(source_id=row.id, pmid=row.pmid, doi=row.doi)
                for row, _ in papers
            ]
        )

    counts = _summarise(verdicts)
    print(
        f"  checked={counts['clean'] + counts['retracted'] + counts['concern']}  "
        f"clean={counts['clean']}  retracted={counts['retracted']}  "
        f"concern={counts['concern']}  unchecked={counts['unchecked']}"
    )

    if counts["unchecked"]:
        # Called out rather than buried in the totals. An unchecked paper is
        # not a clean one, and a sweep that quietly reports "0 retracted" after
        # reaching nothing is exactly the false assurance this feature exists
        # to avoid.
        print(
            f"  ! {counts['unchecked']} source(s) could not be checked — these are "
            "NOT verified clean and will be retried next sweep"
        )

    flagged_articles: dict[str, list[str]] = {}
    for row, article_ids in papers:
        verdict = verdicts.get(row.id, RetractionVerdict())
        if verdict.retracted or verdict.concern:
            label = "RETRACTED" if verdict.retracted else "CONCERN  "
            print(f"  {label} pmid={row.pmid} doi={row.doi} — {row.title[:70]}")
            print(f"            {verdict.detail}")
            if verdict.retracted:
                for article_id in article_ids:
                    flagged_articles.setdefault(article_id, []).append(row.id)

    if flagged_articles:
        print(f"\n  {len(flagged_articles)} article(s) cite a retracted source:")
        for article_id, source_ids in flagged_articles.items():
            print(f"    {article_id}  ({len(source_ids)} source(s))")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return 0

    now = datetime.now(UTC)
    async with factory() as session:
        for row, _ in papers:
            verdict = verdicts.get(row.id, RetractionVerdict())
            if not verdict.checked:
                # No timestamp written. Recording a check that did not happen
                # would suppress the next sweep too, so the error compounds
                # instead of being retried.
                continue
            await session.execute(
                update(Source)
                .where(Source.id == row.id)
                .values(
                    retraction_checked_at=now,
                    retracted_at=now if verdict.retracted else None,
                    concern_at=now if verdict.concern else None,
                    retraction_note=verdict.detail or None,
                )
            )

        for article_id, source_ids in flagged_articles.items():
            await session.execute(
                update(Article)
                .where(Article.id == article_id)
                .values(
                    retraction_flagged_at=now,
                    retraction_detail={
                        "source_ids": source_ids,
                        "detected_at": now.isoformat(),
                    },
                )
            )
        await session.commit()

    print(
        f"\nWrote {counts['clean'] + counts['retracted'] + counts['concern']} check "
        f"result(s) and flagged {len(flagged_articles)} article(s)."
    )
    return 0


async def main() -> int:
    """Wraps ``_run`` so the engine is disposed inside the same event loop.

    Disposing from a ``finally`` around ``asyncio.run`` starts a *second* loop
    and asyncpg's connections belong to the first, which surfaces as "attached
    to a different loop" after the work has already succeeded.
    """
    try:
        return await _run()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
