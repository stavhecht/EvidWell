/**
 * Names for the public feed's styling — masthead, cards, filters, the article
 * page and its source list.
 *
 * Grouped by surface, in reading order, so this file can be scanned the way the
 * product is read rather than the way the components happen to be split.
 *
 * Two things worth knowing before editing:
 *
 * - **The measures are arguments.** `max-w-[16ch]` on the masthead and
 *   `max-w-[64ch]` on the prose are not arbitrary — they are what make the
 *   masthead read as a statement and the body read as an article. Widening
 *   either changes the voice, not just the layout.
 * - **`shadow-panel` and `shadow-pop` are the only shadows in the system.** Both
 *   belong to things that float over the page. Nothing resting on the page gets
 *   one.
 */

/* ── the feed page ──────────────────────────────────────────────────────── */

export const FEED_PAGE = "mx-auto max-w-page px-gutter pb-20";

/** A display-size statement of what the product is, flush left over 16ch. */
export const MASTHEAD = "border-b border-rule-soft pb-[26px] pt-[52px]";
export const MASTHEAD_TITLE =
  "max-w-[16ch] text-balance font-heading text-display font-extrabold text-ink";
export const MASTHEAD_STANDFIRST =
  "mt-[22px] max-w-[56ch] font-body text-standfirst text-ink-2";

export const FEED_FOOTER =
  "mt-14 flex flex-wrap justify-between gap-4 border-t-2 border-rule pt-3.5 font-body text-[12px] leading-normal text-ink-3";

/* ── shared ─────────────────────────────────────────────────────────────── */

/** A small-caps label over a sidebar section or a filter group. */
export const SECTION_LABEL =
  "font-body text-label-sm font-semibold uppercase text-ink-3";
export const SECTION_LABEL_BLOCK = `block ${SECTION_LABEL}`;

/**
 * A text action drawn as an accent underline rather than a button. Used where
 * the action is an escape hatch — clear a filter, go back — and a filled button
 * would compete with the content it sits beside.
 */
export const ACCENT_TEXT_ACTION =
  "border-b border-accent pb-px font-body text-micro font-semibold leading-none text-accent-ink";

/**
 * The article's two-column split: prose left, evidence right. Collapses to one
 * column below `lg`, where a 320px sidebar would leave the prose unreadable.
 */
const COLUMN_GRID =
  "grid grid-cols-1 items-start gap-14 lg:grid-cols-[minmax(0,1fr)_minmax(0,320px)]";

/* ── the feed card ──────────────────────────────────────────────────────── */

/**
 * The 3px top rule is where subject colour lands. It is `border-t-[3px]` on all
 * four sides' border so the rule reads as part of the card's frame rather than
 * as a stripe laid on top of it.
 */
export function feedCard(subjectBorderTop: string): string {
  return `block border border-t-[3px] border-rule-soft bg-surface px-4 pb-3 pt-3.5 transition-colors hover:border-ink-3 ${subjectBorderTop}`;
}

export function cardKicker(subjectText: string): string {
  return `mb-2.5 font-body text-kicker font-semibold uppercase ${subjectText}`;
}

export const CARD_VERDICT_ROW = "flex items-center gap-2";

/** The scope limit, above the headline — the honest half, read first. */
export const CARD_QUALIFIER = "mt-[5px] font-body text-[12px] leading-[1.35] text-ink-3";

export const CARD_HEADLINE =
  "ew-card-headline mt-[11px] text-pretty font-heading leading-[1.18] tracking-[-0.02em] text-ink";

export const CARD_EXCERPT = "mt-[9px] text-pretty font-body text-excerpt text-ink-2";

export const CARD_FOOTER =
  "mt-3.5 border-t border-rule-soft pt-[9px] font-body text-label-sm uppercase text-ink-3";

/* ── the masonry grid and its states ────────────────────────────────────── */

/** Zero-height; the IntersectionObserver target for the next page. */
export const SCROLL_SENTINEL = "h-px";
export const LOADING_MORE = "py-6 font-body text-meta text-ink-3";

/**
 * Skeleton cards, not a spinner: a spinner collapses the layout, so the page
 * jumps the moment content lands. The 3px top rule keeps the grid's structure
 * visible while it fills.
 */
export const SKELETON_GRID =
  "grid grid-cols-[repeat(auto-fill,minmax(320px,1fr))] gap-[18px]";
export const SKELETON_CARD =
  "animate-pulse border border-t-[3px] border-rule-soft border-t-ink-4 bg-surface";

export const FEED_STATE = "border-t border-rule-soft py-16";
export const FEED_STATE_TITLE =
  "font-heading text-[16px] font-semibold leading-tight text-ink";
export const FEED_STATE_BODY = "mt-2 font-body text-excerpt text-ink-3";
export const CLEAR_FILTERS_ACTION = `mt-4 ${ACCENT_TEXT_ACTION}`;
export const RETRY_BUTTON =
  "mt-4 border border-rule-soft px-3.5 py-2.5 font-body text-[12px] font-semibold uppercase leading-none tracking-[0.1em] text-ink-2 transition-colors hover:border-ink hover:text-ink";

/* ── filters ────────────────────────────────────────────────────────────── */

export const FILTER_BAR = "relative flex flex-wrap items-center gap-2.5 pb-[22px] pt-5";

/**
 * Filled ink once anything is on, outlined when nothing is. The fill is what
 * makes a narrowed feed impossible to mistake for an empty one.
 */
export function filterToggle(filtered: boolean): string {
  return `inline-flex items-center gap-2.5 border px-3.5 py-[9px] font-body text-[12px] font-semibold uppercase leading-none tracking-[0.06em] transition-colors hover:border-ink ${
    filtered ? "border-ink bg-ink text-ground" : "border-rule-soft bg-transparent text-ink-2"
  }`;
}

export const FILTER_COUNT = "ml-auto font-body text-[12px] leading-none text-ink-3";

/** Floats over the grid, so z-30 — below the site bar's z-40, never over it. */
export const FILTER_PANEL =
  "absolute left-0 top-[calc(100%-6px)] z-30 w-[min(560px,100%)] border border-t-2 border-rule-soft border-t-rule bg-surface px-5 pb-5 pt-[18px] shadow-panel";
export const FILTER_PANEL_HEAD = "flex items-baseline justify-between gap-3";
export const FILTER_SUBJECT_LABEL = `mt-5 ${SECTION_LABEL_BLOCK}`;
export const CHIP_ROW = "mt-[11px] flex flex-wrap gap-[7px]";

export function filterChip(active: boolean): string {
  return `inline-flex items-center gap-[7px] border px-3 py-[7px] font-body text-[12px] font-semibold leading-none tracking-[0.04em] transition-colors hover:border-ink ${
    active ? "border-ink bg-ink text-ground" : "border-rule-soft bg-transparent text-ink-2"
  }`;
}

/** The subject swatch, shown only while the chip is inactive. */
export function chipDot(subjectBackground: string): string {
  return `h-2 w-2 flex-none ${subjectBackground}`;
}

export const FILTER_PILL =
  "inline-flex items-center gap-2 border border-ink-2 bg-transparent px-[11px] py-2 font-body text-[12px] font-semibold leading-none tracking-[0.03em] text-ink-2 transition-colors hover:bg-surface";

/* ── the article page ───────────────────────────────────────────────────── */

export const ARTICLE_PAGE = "mx-auto max-w-page px-gutter pb-[90px]";
export const ARTICLE_ERROR_PAGE = "mx-auto max-w-page px-gutter py-24";
export const ARTICLE_ERROR_TITLE = "font-heading text-headline font-extrabold text-ink";
export const BACK_TO_FEED_LINK = `mt-4 inline-block ${ACCENT_TEXT_ACTION} uppercase tracking-[0.06em]`;
export const ARTICLE_BACK_LINK =
  "inline-block pt-[22px] font-body text-micro font-semibold uppercase leading-none tracking-[0.11em] text-ink-3 hover:text-ink";

export const ARTICLE_COLUMNS = `mt-[18px] ${COLUMN_GRID}`;
export const ARTICLE_BODY_COLUMN = "min-w-0";

/** The 4px opening rule — the article's counterpart to the card's top rule. */
export function articleVerdictBlock(subjectBorderTop: string): string {
  return `border-t-4 pt-3.5 ${subjectBorderTop}`;
}

export function articleKicker(subjectText: string): string {
  return `mb-[13px] font-body text-label-sm font-semibold uppercase ${subjectText}`;
}

/** The plain-English reading of the verdict. Named `…_TEXT` to stay clear of
 *  `VERDICT_GLOSS` in `evidence/labels`, which holds the wording itself. */
export const VERDICT_GLOSS_TEXT =
  "mt-[9px] max-w-[60ch] font-body text-[13px] leading-normal text-ink-3";
export const ARTICLE_TITLE =
  "mt-[26px] max-w-[22ch] text-balance font-heading text-title font-extrabold text-ink";
export const ARTICLE_LEDE =
  "mt-5 max-w-[60ch] text-pretty font-body text-lede text-ink-2";
export const ARTICLE_META_STRIP =
  "my-[30px] mt-[26px] flex flex-wrap gap-x-[34px] gap-y-4 border-y border-rule-soft py-3.5";
export const META_CELL = "flex min-w-0 flex-col gap-[5px]";
export const META_CELL_LABEL =
  "font-body text-kicker font-semibold uppercase tracking-[0.12em] text-ink-3";
export const META_CELL_VALUE = "font-body text-field text-ink-2";

/** Server-provided, unconditional, and deliberately not model output. */
export const ARTICLE_DISCLAIMER =
  "mt-[34px] max-w-[64ch] border-t border-rule-soft pt-[13px] font-body text-meta text-ink-3";

/**
 * Pinned, because the reader's question during paragraph three is "what is this
 * resting on?" — and making them scroll to the bottom to answer it is how a
 * reader learns not to bother. `top-[86px]` clears the sticky site bar.
 */
export const ARTICLE_SIDEBAR = "min-w-0 lg:sticky lg:top-[86px]";
export const SIDEBAR_BLOCK = "border-t-2 border-rule pt-[13px]";
/** Spacing passed into `GradeBar`'s own `className` slot. */
export const SIDEBAR_GRADE_BAR = "mt-2.5";
export const SIDEBAR_NOTE = "mt-[9px] font-body text-[12px] leading-normal text-ink-3";
export const SIDEBAR_SOURCES = "mt-[26px]";

export const ARTICLE_SKELETON_PAGE =
  "mx-auto max-w-page animate-pulse px-gutter pb-[90px] pt-10";
export const ARTICLE_SKELETON_COLUMNS = COLUMN_GRID;
export const SKELETON_KICKER = "h-6 w-48 bg-surface";
export const SKELETON_TITLE = "mt-6 h-12 w-4/5 bg-surface";
export const SKELETON_LEDE = "mt-3 h-6 w-full bg-surface";
export const SKELETON_PROSE = "mt-8 space-y-3";
export const SKELETON_PARAGRAPH = "h-24 bg-surface";
export const SKELETON_SIDEBAR = "space-y-4";
export const SKELETON_SIDEBAR_BLOCK = "h-16 bg-surface";
export const SKELETON_SOURCE_LIST = "h-52 bg-surface";

/* ── article prose and citation chips ───────────────────────────────────── */

export const PROSE_MEASURE = "max-w-[64ch]";
export const PROSE_PARAGRAPH = "mb-5 text-pretty font-body text-prose text-ink last:mb-0";

/** `whitespace-nowrap` so a chip never wraps away from the word it follows. */
export const CHIP_ANCHOR = "relative whitespace-nowrap";

/**
 * Sized to be pressed. The comp weighed a superscript and a dotted underline;
 * a superscript is a 4px tap target that vanishes at body size, and underlining
 * the sentence makes the *claim* look uncertain rather than its source
 * available. `.ew-chip` supplies the brackets via `::before`/`::after`, so the
 * handle alone is what gets copied and read aloud.
 */
export const CITATION_CHIP =
  "ew-chip ml-0.5 inline-block font-body font-semibold leading-none tracking-[0.03em] text-ink-3 transition-colors hover:border-accent hover:text-accent-ink";

export const SOURCE_POPOVER =
  "absolute left-0 top-[calc(100%+9px)] z-30 block w-[296px] whitespace-normal border border-t-2 border-rule-soft border-t-rule bg-surface px-3.5 pb-3.5 pt-[13px] shadow-pop";
export const POPOVER_KICKER =
  "block font-body text-kicker font-semibold uppercase text-ink-3";
export const POPOVER_TITLE =
  "mt-2 block font-heading text-[14px] font-semibold leading-[1.35] text-ink";
export const POPOVER_META = "mt-[5px] block font-body text-meta text-ink-3";
export const POPOVER_LINK =
  "mt-2.5 inline-block border-b border-accent pb-0.5 font-body text-micro font-semibold uppercase leading-none tracking-[0.06em] text-accent-ink";
export const POPOVER_UNRESOLVED = "mt-2 block font-body text-meta text-ink-3";

/* ── the source list ────────────────────────────────────────────────────── */

export const SOURCES_SECTION = "border-t-2 border-rule pt-3.5";
export const SOURCES_HEAD = "flex items-baseline justify-between gap-2.5";
export const SOURCES_COUNT =
  "font-body text-label leading-none tracking-normal text-ink-3";
export const SOURCES_EMPTY_NOTE = "mt-3 font-body text-micro text-ink-3";
export const SOURCES_FOOTNOTE = "mt-3.5 font-body text-micro text-ink-3";
export const SOURCE_LIST = "mt-3 flex list-none flex-col gap-px p-0";

/**
 * The row the reader just jumped to is washed with the accent, so the anchor
 * lands somewhere visible. `scroll-mt-[90px]` keeps it clear of the site bar.
 */
export function sourceRow(active: boolean): string {
  return `scroll-mt-[90px] border-l-2 px-3 py-[11px] transition-colors ${
    active ? "border-l-accent bg-accent-wash" : "border-l-rule-soft bg-surface"
  }`;
}

export const SOURCE_ROW_HEAD = "flex items-baseline gap-2";
export const SOURCE_HANDLE =
  "flex-none font-body text-label font-semibold leading-[1.3] tracking-[0.06em] text-ink-3";
export const SOURCE_TITLE_LINK =
  "text-pretty font-body text-[13px] font-semibold leading-[1.35] text-ink hover:text-accent-ink";

/**
 * Weak study types are drawn in the accent ink — the one place colour touches
 * evidence, and it flags the *source*, never the verdict.
 */
export function sourceStudyType(weak: boolean): string {
  return `ml-[26px] mt-1 font-body text-micro ${weak ? "text-accent-ink" : "text-ink-3"}`;
}

export const SOURCE_META = "ml-[26px] font-body text-micro text-ink-3";

export const CITATION_MAP = "mt-4";
export const CITATION_MAP_SUMMARY =
  "cursor-pointer font-body text-micro text-ink-3 hover:text-ink";
export const CITATION_MAP_LIST = "mt-2 flex list-none flex-col gap-1.5 p-0";
export const CITATION_MAP_ITEM = "font-body text-micro text-ink-3";
export const CITATION_MAP_CLAIM = "font-semibold text-ink-2";
export const CITATION_MAP_HANDLE = "underline";
