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
 * Set by a reviewer, never inferred — it cannot be derived from `product`,
 * which is free text, and a guessed subject would put a confident colour on an
 * unchecked classification. Still optional everywhere: an unclassified article
 * renders in ink, which is the design's resting state rather than a gap.
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
  /**
   * The article's own first picture, derived at publish time — never a
   * separately-uploaded thumbnail, so a tile cannot show something the article
   * does not. Null is the normal case, and the tile falls back to type.
   */
  image: string | null;
  imageAlt: string | null;
  publishedAt: string;
  subject: Subject | null;
}

/** A feed card plus the folder it sits on. Only ever from `/readers/saved`. */
export interface SavedCard extends FeedCard {
  folderId: string;
}

/**
 * The feed tile a draft *would* publish as. Console-only.
 *
 * Extends {@link FeedCard} rather than restating it, because the point of the
 * preview is that it renders through the same `ArticleCard` the public feed
 * uses. A shape of its own would let the two drift, and the drift is exactly
 * what the preview exists to catch.
 *
 * Derived server-side by the same `derive_card()` the publish path runs — see
 * `fetchCardPreview`.
 */
export interface CardPreview extends FeedCard {
  /**
   * True when the picture is the portrait cover the pipeline generated rather
   * than the article's own first image. The tile a reviewer is looking at is
   * then a different crop from the picture in the editor beside it, which
   * reads as a bug unless it is said out loud.
   */
  imageIsGeneratedCover: boolean;
}

/** One frame of a generated illustration. */
export interface GeneratedFrame {
  src: string;
  alt: string;
}

/**
 * Which of an article's two pictures. They live in different places and are
 * judged separately, so they can be redrawn separately.
 */
export type IllustrationFrame = "lead" | "cover";

/**
 * Both frames after a regenerate — always both, whether or not both were drawn.
 *
 * `lead` goes into the document — the client swaps the editor's image node and
 * lets autosave persist it, so the new `src` passes the server's media check
 * like any other edit. `cover` is already recorded on the article and reaches
 * the feed tile only while `lead` is still the document's first picture.
 *
 * The server answers with both even for a one-frame redraw, because the client's
 * job is to make the document agree with the article row and it cannot do that
 * from a partial answer.
 */
export interface GeneratedImagery {
  lead: GeneratedFrame;
  cover: GeneratedFrame;
  /** Which frames this call actually drew, as the server reports them. */
  redrawn: IllustrationFrame[];
}

export interface FeedPage {
  items: FeedCard[];
  nextCursor: string | null;
}

/**
 * Published counts per subject and per verdict, for the browse drawer.
 *
 * Both maps are **sparse** — a key is absent when nothing is published under
 * it — so read them with a `?? 0`, never by assuming the enum is populated.
 * They also do not sum to `total`: an unclassified article is counted in the
 * total and in no subject, which is why "Everything" is genuinely larger than
 * the five categories added up.
 */
export interface FeedFacets {
  total: number;
  subjects: Partial<Record<Subject, number>>;
  verdicts: Partial<Record<Verdict, number>>;
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
  subject: Subject | null;
  publishedAt: string;
  /**
   * A cited source has been retracted since publication. The article stays up
   * and stays readable — one withdrawn source does not necessarily invalidate
   * a conclusion, and a human decides — but the reader is told first.
   */
  retractionNotice: boolean;
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
  subject: Subject | null;
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
  /**
   * Where the article lives on the public feed once published. Sent for every
   * draft — the slug is assigned at persist time — so the console gates the
   * "view on the feed" link on `status`, not on this being set.
   */
  slug: string;
  subject: Subject | null;
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

// --- reader accounts -------------------------------------------------------
//
// The public side's own auth surface. A reader is not a `Reviewer` with fewer
// permissions — it is a different table, a different token, and a type that
// carries no role at all, so no component can branch on one and be handed the
// other.

export interface Reader {
  id: string;
  email: string;
  displayName: string;
  /** Subjects lifted to the top of this reader's feed. Never a filter. */
  interests: Subject[];
  newsletter: boolean;
}

export interface Folder {
  id: string;
  name: string;
  /** Articles on this shelf. Server-counted, so the tab label cannot drift. */
  count: number;
}

export interface SavedPage {
  folders: Folder[];
  items: SavedCard[];
}

/**
 * `{ slug: folderId }` for everything the signed-in reader has saved.
 *
 * Fetched once alongside the feed rather than per tile, and deliberately not
 * folded into the feed response: the feed is public, identical for everyone
 * and cacheable, and a per-reader field in it would make it none of those.
 */
export type SavedIndex = Record<string, string>;

export type ContactKind = "fact_check" | "topic" | "other";

export interface ContactSubmission {
  kind: ContactKind;
  name?: string | null;
  email: string;
  link?: string | null;
  note: string;
}
