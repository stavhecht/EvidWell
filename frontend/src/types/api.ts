/**
 * Mirrors the backend response models.
 *
 * Hand-maintained for now. Once the API stabilises, generate these from the
 * OpenAPI schema FastAPI already produces — hand-written mirrors of a contract
 * drift, and they drift silently, because TypeScript happily validates against
 * a stale definition.
 *
 * Sources: backend/app/api/public/schemas.py, backend/app/api/console/schemas.py
 */

export type Verdict = "supported" | "mixed" | "weak" | "no_evidence";

export type StudyType =
  | "unknown"
  | "in_vitro"
  | "animal"
  | "case_report"
  | "observational"
  | "rct"
  | "systematic_review"
  | "meta_analysis";

export type ArticleStatus =
  | "pending_review"
  | "published"
  | "rejected"
  | "validation_failed"
  | "draft_failed";

/**
 * TipTap document node.
 *
 * Structurally compatible with TipTap's own `JSONContent`, so documents pass
 * into the editor without a cast — but declared here rather than imported from
 * `@tiptap/core`, so the public feed's renderer does not pull the editor
 * package into its bundle.
 */
export interface TipTapNode {
  type?: string;
  attrs?: Record<string, unknown>;
  content?: TipTapNode[];
  marks?: { type: string; attrs?: Record<string, unknown> }[];
  text?: string;
}

export interface TipTapDoc extends TipTapNode {
  type: "doc";
  content?: TipTapNode[];
}

// --- public ----------------------------------------------------------------

export interface FeedCard {
  slug: string;
  headline: string;
  excerpt: string;
  verdict: Verdict;
  verdictQualifier: string | null;
  publishedAt: string;
}

export interface FeedPage {
  items: FeedCard[];
  nextCursor: string | null;
}

export interface Source {
  title: string;
  journal: string | null;
  year: number | null;
  studyType: StudyType;
  citationHandle: string;
  url: string;
  pmid: string | null;
  doi: string | null;
}

export interface Citation {
  claim: string;
  handles: string[];
}

export interface Article {
  slug: string;
  headline: string;
  summary: string;
  verdict: Verdict;
  verdictQualifier: string | null;
  product: string;
  targetClaims: string[];
  ingredients: string[];
  content: TipTapDoc;
  sources: Source[];
  citations: Citation[];
  evidenceGrade: StudyType;
  publishedAt: string;
  disclaimer: string;
}

// --- console ---------------------------------------------------------------

export interface QueueItem {
  id: string;
  topic: string;
  headline: string;
  verdict: Verdict;
  status: ArticleStatus;
  evidenceGrade: StudyType;
  /** e.g. "4/4 citations resolve". Computed server-side at draft time. */
  validationBadge: string;
  hasWeakEvidence: boolean;
  createdAt: string;
}

export interface ReviewSource extends Source {
  sourceId: string;
  claim: string;
  citationCount: number | null;
  /** False for sources retrieved but not cited — shown greyed, not hidden. */
  wasCited: boolean;
  relevanceScore: number | null;
  isWeakEvidence: boolean;
}

export interface ValidationFailure {
  code: string;
  message: string;
  detail: Record<string, unknown>;
}

export interface ValidationReport {
  passed: boolean;
  citationsTotal: number;
  citationsResolved: number;
  bestEvidenceGrade: StudyType;
  failures: ValidationFailure[];
}

export interface ArticleDetail {
  id: string;
  status: ArticleStatus;
  topic: string;
  product: string;
  targetClaims: string[];
  ingredients: string[];
  headline: string;
  summary: string;
  verdict: Verdict;
  verdictQualifier: string | null;
  evidenceGrade: StudyType;
  /** The immutable AI draft. Never sent back to the server. */
  originalContent: TipTapDoc;
  /** Null until the first autosave. The editor loads `edited ?? original`. */
  editedContent: TipTapDoc | null;
  validationReport: ValidationReport;
  sources: ReviewSource[];
  pipelineRunId: string | null;
  createdAt: string;
}

export interface Reviewer {
  id: string;
  email: string;
  displayName: string;
  role: "admin" | "reviewer";
}
