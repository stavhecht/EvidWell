"""Editorial console request/response models.

The console sees everything the public surface hides: draft content, the
validation report, retrieved-but-uncited sources, pipeline run history.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
    #: The literature has withdrawn this paper. Distinct from weak evidence:
    #: weak means the study is a poor basis for confidence, retracted means it
    #: is not a basis at all.
    retracted: bool = False
    #: Under investigation, not withdrawn. Recorded rather than refused,
    #: because the paper may yet be exonerated.
    concern: bool = False
    #: Which provider said so, e.g. "pubmed: retracted publication".
    retraction_note: str | None = None


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
    #: Set when a cited source has been retracted since this article was
    #: written. The article keeps its status — this raises it for a human,
    #: it does not withdraw it.
    retraction_flagged_at: datetime | None = None
    retraction_detail: dict | None = None
    created_at: datetime


class SaveContentRequest(CamelModel):
    """Autosave payload. Debounced ~800ms from the editor."""

    content: dict = Field(description="TipTap document")


class MediaUploadOut(CamelModel):
    """Where a just-uploaded image now lives.

    Only a path comes back. There is no id and no record: the store is
    content-addressed, so the path *is* the identity, and an image is
    referenced only from the document that embeds it.
    """

    #: Origin-relative, e.g. ``/api/media/1f/2a….png``. Goes verbatim into the
    #: editor's image node, and is the only ``src`` shape the server will
    #: accept back.
    src: str
    #: Sniffed from the bytes, not taken from the upload's Content-Type.
    content_type: str
    bytes: int


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
    #: Provider-namespaced model this stage called; null for the four that call
    #: none. Recorded from the call itself, so it is what actually ran rather
    #: than what the settings say now.
    model: str | None
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    #: Computed at read time from app/llm/pricing.py, never stored. Null means
    #: the model is not in the price table — which is *not* zero, and reads in
    #: the console as "unknown" so a newly-pointed-at model cannot look free.
    estimated_cost_usd: Decimal | None
    started_at: datetime | None
    finished_at: datetime | None


class RunOut(CamelModel):
    id: str
    topic: str
    status: RunStatus
    article_id: str | None
    error: dict | None
    #: Lifetime totals across every attempt, so a run that retried reports what
    #: it really spent. Not priceable as a unit — the stages may have run on
    #: different models — which is why the cost below is summed per stage.
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    #: Sum of the per-stage costs, or null if *any* stage that consumed tokens
    #: ran on an unpriced model. All-or-nothing: a partial sum is a plausible
    #: number that is wrong by an order of magnitude, and the per-stage figures
    #: stay visible either way.
    estimated_cost_usd: Decimal | None
    #: Attempts started. >1 means a retryable failure requeued this run; the
    #: stages below are the latest attempt.
    attempts: int
    #: When the run is queued and waiting out a retry backoff. Null otherwise —
    #: distinguishes "waiting deliberately" from "the worker is not running".
    next_attempt_at: datetime | None
    #: Last sign of life while `running`. A timestamp minutes old means the
    #: worker died and the sweep has not reached the run yet — without it a
    #: dead run and a slow one look identical in the console.
    heartbeat_at: datetime | None
    stages: list[StageRunOut]
    created_at: datetime
    finished_at: datetime | None


class RunPageOut(CamelModel):
    items: list[RunOut]
    next_cursor: str | None = None
