"""Editorial console request/response models.

The console sees everything the public surface hides: draft content, the
validation report, retrieved-but-uncited sources, pipeline run history.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.api.schemas_base import CamelModel
from app.domain.enums import ArticleStatus, RunStatus, StudyType, UserRole, Verdict

# --- auth ------------------------------------------------------------------


class LoginRequest(CamelModel):
    email: str
    password: str


class TokenResponse(BaseModel):
    """Deliberately **not** camelCased.

    ``access_token`` / ``token_type`` / ``expires_in`` are the OAuth 2 bearer
    response field names. Renaming them to fit our house style would break
    every off-the-shelf client for no gain.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int


class ReviewerOut(CamelModel):
    id: str
    email: str
    display_name: str
    role: UserRole


# --- review queue ----------------------------------------------------------


class QueueItemOut(CamelModel):
    id: str
    topic: str
    headline: str
    verdict: Verdict
    status: ArticleStatus
    evidence_grade: StudyType
    #: '4/4 citations resolve'. Computed at draft time, stored, not recomputed.
    validation_badge: str
    #: True when the verdict rests on in-vitro/animal/case-report evidence.
    #: Surfaced in the list so a reviewer can triage before opening anything.
    has_weak_evidence: bool
    created_at: datetime


class QueuePageOut(CamelModel):
    items: list[QueueItemOut]
    next_cursor: str | None = None


class ReviewSourceOut(CamelModel):
    """A source in the review panel — richer than the public equivalent."""

    source_id: str
    citation_handle: str
    claim: str
    title: str
    journal: str | None
    year: int | None
    study_type: StudyType
    citation_count: int | None
    url: str
    pmid: str | None
    doi: str | None
    #: False for sources retrieved but not cited. Shown anyway, greyed: a
    #: reviewer needs to see what the model chose to leave out, because
    #: omission is the failure mode human review exists to catch.
    was_cited: bool
    relevance_score: float | None
    #: Drives the inline warning marker in the sources panel.
    is_weak_evidence: bool


class ValidationFailureOut(CamelModel):
    code: str
    message: str
    detail: dict = Field(default_factory=dict)


class ValidationReportOut(CamelModel):
    """API-facing mirror of ``domain.contracts.ValidationReport``.

    Separate rather than reusing the domain model because that one is also
    persisted verbatim into ``articles.validation_report``. Aliasing its fields
    to camelCase would silently change the stored JSON and break every reader
    of existing rows.
    """

    passed: bool
    citations_total: int
    citations_resolved: int
    best_evidence_grade: StudyType
    failures: list[ValidationFailureOut] = Field(default_factory=list)


class ArticleDetailOut(CamelModel):
    """Everything the review screen needs, in one request."""

    id: str
    status: ArticleStatus
    topic: str
    product: str
    target_claims: list[str]
    ingredients: list[str]
    headline: str
    summary: str
    verdict: Verdict
    verdict_qualifier: str | None
    evidence_grade: StudyType
    #: The immutable AI draft. Rendered read-only beside the editor.
    original_content: dict
    #: Human edits, null until first autosave. The editor loads
    #: ``edited_content or original_content``.
    edited_content: dict | None
    validation_report: ValidationReportOut
    #: Grouped by claim in handle order, so the panel renders without
    #: client-side joining.
    sources: list[ReviewSourceOut]
    pipeline_run_id: str | None
    created_at: datetime


class SaveContentRequest(CamelModel):
    """Autosave payload. Debounced ~800ms from the editor."""

    content: dict = Field(description="TipTap document")


class RejectRequest(CamelModel):
    #: Required. Rejection reasons are the best available signal for improving
    #: the synthesis prompt, so the API refuses to discard one.
    reason: str = Field(min_length=1, max_length=2000)


# --- pipeline --------------------------------------------------------------


class CreateRunRequest(CamelModel):
    topic: str = Field(min_length=1, description="e.g. 'ashwagandha for stress'")
    blurb: str | None = Field(default=None, description="Optional marketing copy")


class StageRunOut(CamelModel):
    stage: str
    ordinal: int
    status: RunStatus
    error: dict | None
    metrics: dict | None
    started_at: datetime | None
    finished_at: datetime | None


class RunOut(CamelModel):
    id: str
    topic: str
    status: RunStatus
    article_id: str | None
    error: dict | None
    input_tokens: int
    output_tokens: int
    stages: list[StageRunOut]
    created_at: datetime
    finished_at: datetime | None


class RunPageOut(CamelModel):
    items: list[RunOut]
    next_cursor: str | None = None
