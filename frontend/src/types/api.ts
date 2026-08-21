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
  | "narrative_review"
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
 * What kind of thing is being assessed. Drives the one chromatic axis in the
 * design (see `features/evidence/subject.ts`).
 *
 * PROPOSED — the server does not send this yet. It is optional on every model
 * below and every consumer degrades to ink when it is absent, so the field can
 * land without a coordinated release. Shipping it needs an editor-set enum on
 * the article model: it cannot be derived from `product`, which is free text.
 */
export type Subject = "supplement" | "device" | "protocol" | "food" | "topical";

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
  /** Proposed; see {@link Subject}. Absent today. */
  subject?: Subject | null;
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
  /**
   * The literature has withdrawn this paper since the article cited it. Shown
   * on the source itself, not only in the article banner — a reader who
   * scrolls to the citations should not have to infer which one it was.
   */
  retracted: boolean;
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
  /**
   * A cited source has been retracted since publication. The article stays up
   * and stays readable — one withdrawn source does not necessarily invalidate
   * a conclusion, and a human decides — but the reader is told first.
   */
  retractionNotice: boolean;
  disclaimer: string;
  /** Proposed; see {@link Subject}. Absent today. */
  subject?: Subject | null;
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
  /** Proposed; see {@link Subject}. Absent today. */
  subject?: Subject | null;
}

export interface ReviewSource extends Source {
  sourceId: string;
  claim: string;
  citationCount: number | null;
  /** False for sources retrieved but not cited — shown greyed, not hidden. */
  wasCited: boolean;
  relevanceScore: number | null;
  isWeakEvidence: boolean;
  /**
   * Distinct from {@link isWeakEvidence}: weak means a poor basis for
   * confidence, retracted means not a basis at all.
   */
  retracted: boolean;
  /** Under investigation, not withdrawn. Recorded, not refused. */
  concern: boolean;
  /** Which provider said so, e.g. "pubmed: retracted publication". */
  retractionNote: string | null;
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
  /**
   * Set when a cited source has been retracted since this article was written.
   * The article keeps its status — this raises it for a human, it does not
   * withdraw it.
   */
  retractionFlaggedAt: string | null;
  retractionDetail: Record<string, unknown> | null;
  createdAt: string;
}

/**
 * A just-uploaded image. No id and no record: the store is content-addressed,
 * so the path is the identity, and an image is referenced only by the document
 * that embeds it.
 */
export interface MediaUpload {
  /** Origin-relative, e.g. `/api/media/1f/2a….png`. Goes into the image node. */
  src: string;
  /** Sniffed from the bytes — not the Content-Type the upload claimed. */
  contentType: string;
  bytes: number;
}

export interface Reviewer {
  id: string;
  email: string;
  displayName: string;
  role: "admin" | "reviewer";
}

// --- pipeline --------------------------------------------------------------

export type RunStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export interface StageRun {
  stage: string;
  ordinal: number;
  status: RunStatus;
  error: Record<string, unknown> | null;
  metrics: Record<string, unknown> | null;
  /**
   * Provider-namespaced model this stage called (`anthropic/claude-sonnet-5`),
   * or null for the four stages that call no model. Recorded from the call, so
   * it is what actually ran rather than what the settings say now.
   */
  model: string | null;
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  /**
   * Decimal USD, serialised as a string so it survives the trip without
   * binary-float drift. **Null means unknown, not free** — the model is not in
   * the backend price table. Render it as "unknown"; a `?? 0` here would make
   * a newly-configured model look like a local one.
   */
  estimatedCostUsd: string | null;
  startedAt: string | null;
  finishedAt: string | null;
}

/**
 * A generation run. Newest first from the API.
 *
 * `queued` and `running` are the only states the console renders — they are
 * what stands between "Generate draft" and a row in the review queue, and
 * without them the reviewer stares at an unchanged queue for several minutes.
 */
export interface PipelineRun {
  id: string;
  topic: string;
  status: RunStatus;
  articleId: string | null;
  error: Record<string, unknown> | null;
  /**
   * Lifetime totals across every attempt, so a retried run reports what it
   * really spent. `stages` below is the latest attempt only, so these will
   * exceed the stage figures whenever `attempts > 1`.
   */
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheWriteTokens: number;
  /**
   * Decimal USD as a string, summed over the latest attempt's stages. Null if
   * any stage that consumed tokens ran on an unpriced model — all-or-nothing,
   * because a partial sum is a plausible number that is wrong by an order of
   * magnitude. Same rule as above: null is unknown, not zero.
   */
  estimatedCostUsd: string | null;
  /** Attempts started. >1 means a retryable failure requeued this run. */
  attempts: number;
  /** Set while a run is queued waiting out a retry backoff; null otherwise. */
  nextAttemptAt: string | null;
  /** Last sign of life while `running`. Minutes old means the worker died. */
  heartbeatAt: string | null;
  stages: StageRun[];
  createdAt: string;
  finishedAt: string | null;
}

export interface RunPage {
  items: PipelineRun[];
  nextCursor: string | null;
}
