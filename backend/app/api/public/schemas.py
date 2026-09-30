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

from pydantic import BaseModel, Field

from app.api.schemas_base import CamelModel, EmailAddress
from app.domain.enums import ContactKind, StudyType, Subject, Verdict


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
    #: The literature has withdrawn this paper since we cited it. Shown to the
    #: reader on the source itself, not only in the article banner — a reader
    #: who scrolls to the citations should not have to infer which one it was.
    retracted: bool = False


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
    #: The article's own first picture, materialised at publish time. Null is
    #: the normal case and not a missing asset — the tile falls back to type.
    image: str | None = None
    image_alt: str | None = None
    #: What kind of thing this assesses. Null until a reviewer classifies it;
    #: the tile then renders in ink rather than in a subject hue.
    subject: Subject | None = None
    published_at: datetime


class FeedFacetsOut(CamelModel):
    """Counts for the browse drawer.

    ``subjects`` and ``verdicts`` are sparse — a key is absent when nothing is
    published under it, so a client must default to zero rather than assume the
    enum is fully populated. They also do **not** sum to ``total``: an
    unclassified article is counted in the total and in no subject.
    """

    total: int
    subjects: dict[str, int]
    verdicts: dict[str, int]


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
    subject: Subject | None = None
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
    #: Set when a cited source has been retracted since publication. The
    #: article stays up and stays readable — a human decides whether the
    #: conclusion still holds — but the reader is told before they read it.
    #: See scripts/check_retractions.py.
    retraction_notice: bool = False
    #: Set from a server constant, never from model output — the model cannot
    #: forget it, reword it, or drop it.
    disclaimer: str = Field(
        default="This article is informational and is not medical advice. "
        "Talk to a qualified professional about your own situation."
    )


# --- reader accounts -------------------------------------------------------
#
# The public side's own auth surface. Nothing here overlaps the console's: a
# reader has no role, sees no draft, and its token is minted with a different
# `typ` claim so it cannot be presented to /api/console at all.


class SignupRequest(CamelModel):
    email: EmailAddress
    #: Floor only. A ceiling belongs here too once a password policy exists;
    #: what must not appear is a *low* ceiling, which is the classic tell that
    #: something downstream is storing the plaintext.
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(min_length=1, max_length=80)
    interests: list[Subject] = Field(default_factory=list)
    newsletter: bool = False


class ReaderLoginRequest(CamelModel):
    email: EmailAddress
    password: str = Field(min_length=1, max_length=200)


class ReaderTokenResponse(BaseModel):
    """OAuth 2's snake_case field names, deliberately not camelCased.

    Same reasoning as the console's ``TokenResponse``: that shape is a
    standard, not our house style.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ReaderOut(CamelModel):
    id: str
    email: str
    display_name: str
    interests: list[Subject]
    newsletter: bool


class UpdateReaderRequest(CamelModel):
    """Patch semantics: an omitted field is left alone.

    An empty ``interests`` list is therefore a real instruction — turn
    personalisation off — and is distinct from not sending the field.
    """

    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    interests: list[Subject] | None = None
    newsletter: bool | None = None


class FolderOut(CamelModel):
    id: str
    name: str
    count: int


class CreateFolderRequest(CamelModel):
    name: str = Field(min_length=1, max_length=60)


class SaveRequest(CamelModel):
    """Which shelf to put it on. Omitted means the reader's first folder."""

    folder_id: str | None = None


class SavedCardOut(FeedCardOut):
    """A feed card plus which folder it is on, so the grid can render both."""

    folder_id: str


class SavedPageOut(CamelModel):
    folders: list[FolderOut]
    items: list[SavedCardOut]


# --- contact ---------------------------------------------------------------


class ContactRequestIn(CamelModel):
    kind: ContactKind = ContactKind.OTHER
    name: str | None = Field(default=None, max_length=80)
    #: Required even though the form calls the name optional: without a way to
    #: reply, a request we can answer is indistinguishable from one we cannot.
    email: EmailAddress
    link: str | None = Field(default=None, max_length=2000)
    note: str = Field(min_length=1, max_length=4000)
