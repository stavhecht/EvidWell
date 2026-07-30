"""Domain enumerations.

These mirror the Postgres enum types in migrations/0001_initial.sql. Keep the
two in sync — a value added here without a matching ``ALTER TYPE`` will fail at
write time, not at import time.
"""

from __future__ import annotations

from enum import StrEnum


class ArticleStatus(StrEnum):
    """Lifecycle of an article.

    ``validation_failed`` and ``draft_failed`` are terminal *pre-queue* states.
    They are never reachable from ``pending_review`` and never appear in the
    review queue — they exist so a failed draft is visible rather than silent.
    """

    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    REJECTED = "rejected"
    VALIDATION_FAILED = "validation_failed"
    DRAFT_FAILED = "draft_failed"


class Verdict(StrEnum):
    """Plain-language verdict on whether the evidence supports the claims."""

    SUPPORTED = "supported"
    MIXED = "mixed"
    WEAK = "weak"
    NO_EVIDENCE = "no_evidence"


class StudyType(StrEnum):
    """Evidence-quality hierarchy, weakest to strongest.

    Declaration order is the ranking and is load-bearing: ``EVIDENCE_RANK``
    below, the verdict cap, and the Postgres enum ordering all depend on it.

    ``UNKNOWN`` deliberately sits at the bottom. When a provider gives us no
    usable publication type, uncertainty must *cap* confidence rather than
    inflate it — an unclassifiable source is treated as the weakest evidence,
    not given the benefit of the doubt.
    """

    UNKNOWN = "unknown"
    IN_VITRO = "in_vitro"
    ANIMAL = "animal"
    CASE_REPORT = "case_report"
    OBSERVATIONAL = "observational"
    RCT = "rct"
    SYSTEMATIC_REVIEW = "systematic_review"
    META_ANALYSIS = "meta_analysis"


#: Numeric rank for comparisons. Higher is stronger evidence.
EVIDENCE_RANK: dict[StudyType, int] = {
    study_type: rank for rank, study_type in enumerate(StudyType)
}


class UserRole(StrEnum):
    ADMIN = "admin"
    REVIEWER = "reviewer"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class SourceApi(StrEnum):
    """Which scholarly API a source came from.

    Google Scholar is absent by design — no scraping. SerpApi is reserved as a
    later fallback and is not part of the MVP.
    """

    PUBMED = "pubmed"
    EUROPE_PMC = "europe_pmc"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    OPENALEX = "openalex"
