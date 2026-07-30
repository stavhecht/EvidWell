"""Public API response models.

These define the contract the frontend types mirror
(``frontend/src/types/api.ts``). Nothing here exposes anything about the
pipeline, the model, or the review process — the public surface knows only
about published articles.

Note what is absent: no ``status`` field (everything here is published by
definition), no ``original_content`` (the draft is an internal artifact), no
reviewer identity.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.api.schemas_base import CamelModel
from app.domain.enums import StudyType, Verdict


class SourceOut(CamelModel):
    """A cited paper, as shown under an article."""

    title: str
    journal: str | None
    year: int | None
    study_type: StudyType
    citation_handle: str
    #: Resolved link — DOI URL preferred, PubMed as fallback. Guaranteed
    #: non-null: a source without a resolvable identifier cannot be cited.
    url: str
    pmid: str | None
    doi: str | None


class CitationOut(CamelModel):
    claim: str
    handles: list[str]


class FeedCardOut(CamelModel):
    """One masonry card. Derived from the article, never generated."""

    slug: str
    headline: str
    excerpt: str
    verdict: Verdict
    verdict_qualifier: str | None
    published_at: datetime


class FeedPageOut(CamelModel):
    items: list[FeedCardOut]
    #: Opaque cursor. Null when there are no further pages. Cursor-based rather
    #: than offset-based because the feed is append-ish and offsets skip or
    #: repeat items when something publishes mid-scroll.
    next_cursor: str | None = None


class ArticleOut(CamelModel):
    """The full on-tap article."""

    slug: str
    headline: str
    summary: str
    verdict: Verdict
    verdict_qualifier: str | None
    product: str
    target_claims: list[str]
    ingredients: list[str]
    #: TipTap document. Citation nodes carry ``sourceIds`` matching
    #: ``SourceOut.citation_handle``.
    content: dict
    sources: list[SourceOut]
    citations: list[CitationOut]
    evidence_grade: StudyType
    published_at: datetime
    #: Set from a server constant, never from model output — the model cannot
    #: forget it, reword it, or drop it.
    disclaimer: str = Field(
        default="This article is informational and is not medical advice. "
        "Talk to a qualified professional about your own situation."
    )
