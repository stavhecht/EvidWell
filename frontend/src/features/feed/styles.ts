/**
 * Names for the public feed's styling — hero, tiles, the article page and its
 * source list.
 *
 * Grouped by surface, in reading order, so this file can be scanned the way the
 * product is read rather than the way the components happen to be split.
 *
 * Three things worth knowing before editing:
 *
 * - **The measures are arguments.** `max-w-prose` (760px) on the article and
 *   `max-w-[64ch]` on its prose are not arbitrary — they are what make the body
 *   read as an article rather than as a page. Widening either changes the
 *   voice, not just the layout.
 * - **`shadow-panel` and `shadow-pop` are the only shadows in the system.** Both
 *   belong to things that float over the page. Nothing resting on the page gets
 *   one; the tiles use radius and a scrim instead.
 * - **Tile text is white, not ink.** It sits over a photograph nobody controls,
 *   so it is the one place in the product where a fixed colour beats a token —
 *   see `.ew-tile-scrim` in `styles/youth.css`.
 */

import { mediaWrapClass, type MediaAlign } from "@/lib/media";

/* ── the feed page ──────────────────────────────────────────────────────── */

export const FEED_PAGE = "pb-16";

/**
 * The hero, full-bleed.
 *
 * `w-screen` with a negative margin rather than a wrapper without the page
 * measure: the video runs edge to edge while everything under it stays on the
 * 1240px grid, and `calc(50% - 50vw)` is what pulls it out of the centred
 * column without a layout wrapper of its own.
 */
export const HERO =
  "relative ml-[calc(50%-50vw)] aspect-[16/9] max-h-[68vh] w-screen overflow-hidden bg-tile";
export const HERO_VIDEO = "h-full w-full object-cover saturate-[0.72]";

/**
 * The one Playfair element in the product.
 *
 * Sits at 70% opacity over the footage rather than at full white: the statement
 * is a mood, and type at full strength over moving video reads as a caption
 * demanding to be finished before the picture is looked at.
 */
export const HERO_STATEMENT =
  "pointer-events-none absolute left-1/2 top-1/2 w-[min(90%,16ch)] -translate-x-1/2 -translate-y-1/2 text-balance text-center font-display text-[clamp(28px,3.6vw,60px)] font-bold uppercase leading-[1.1] text-[#f4f0ed] opacity-70";

export const FEED_BODY = "mx-auto max-w-page px-gutter";

export const FEED_HEAD =
  "flex flex-wrap items-baseline justify-between gap-3 pb-[18px] pt-7";
export const FEED_TITLE =
  "font-heading text-[22px] font-bold tracking-[-0.02em] text-ink";
export const FEED_COUNT = "font-body text-[12.5px] text-ink-3";

export const FEED_ACTIONS = "flex flex-wrap items-center gap-2";

/** A pill that clears or toggles something. Outlined — never the accent fill. */
export const PILL_BUTTON =
  "inline-flex items-center gap-2 rounded-full border border-rule bg-transparent px-3.5 py-[7px] font-body text-[10.5px] font-semibold uppercase leading-none tracking-[0.12em] text-ink transition-colors hover:border-ink";

/** The same pill, filled, for a narrowing that is currently on. */
export const PILL_BUTTON_ON =
  "inline-flex items-center gap-2 rounded-full border border-invert bg-invert px-3.5 py-[7px] font-body text-[10.5px] font-semibold uppercase leading-none tracking-[0.12em] text-invert-fg transition-opacity hover:opacity-90";

/**
 * The standing promise under the feed.
 *
 * Centred and on a narrow measure, which is the one place the product centres
 * body copy: it is a statement about the whole page rather than part of the
 * reading flow.
 */
export const FEED_FOOTER =
  "mx-auto mt-16 max-w-[60ch] border-t border-rule-soft pt-[22px] text-center font-body text-[15px] leading-normal text-ink-2";

/* ── shared ─────────────────────────────────────────────────────────────── */

/** A small-caps label over a section. */
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

/* ── the feed tile ──────────────────────────────────────────────────────── */

/**
 * One tile. The aspect ratio is set inline per card — see `tileRatio` in
 * `ArticleCard.tsx` — because it varies, which is what gives the masonry its
 * rhythm.
 */
export const TILE = "relative overflow-hidden rounded-tile bg-tile";
export const TILE_LINK = "absolute inset-0 block";
export const TILE_IMAGE = "h-full w-full object-cover";

/**
 * The typographic fallback, for an article with no picture.
 *
 * Not a placeholder graphic and not a grey box: the headline is set large on
 * the tile ground, so a tile without an image is a legitimate design rather
 * than a missing asset. This is the normal case, not the exception.
 */
export const TILE_TYPESET =
  "flex h-full w-full flex-col justify-end gap-2 p-4";
export const TILE_TYPESET_HEADLINE =
  "text-pretty font-heading text-[15px] font-bold leading-[1.2] tracking-[-0.015em] text-ink";
export const TILE_TYPESET_KICKER =
  "font-body text-kicker font-bold uppercase text-ink-3";

/** The scrim and the text over it. `pointer-events-none` so the link wins. */
export const TILE_SCRIM =
  "ew-tile-scrim pointer-events-none absolute inset-x-0 bottom-0 z-[3] max-h-full pt-9";
export const TILE_TEXT = "px-3 pb-3";
export const TILE_KICKER =
  "mb-1 flex items-center gap-1.5 font-body text-[8.5px] font-bold uppercase leading-none tracking-[0.13em] text-[rgb(255_253_250/0.78)]";
export const TILE_QUALIFIER =
  "mb-1 line-clamp-1 font-body text-[9px] leading-tight text-[rgb(255_253_250/0.66)]";
export const TILE_HEADLINE =
  "line-clamp-3 text-pretty font-heading text-[11.5px] font-bold leading-[1.28] tracking-[-0.012em] text-[#fffdfa]";

/**
 * The Save control, floated over the tile.
 *
 * `z-[4]` puts it above both the scrim and the link overlay, which is what
 * makes it pressable at all — everything below it is one big anchor.
 */
export function tileSaveButton(saved: boolean): string {
  return `absolute right-2 top-2 z-[4] rounded-full border-0 px-[11px] py-1.5 font-body text-[9.5px] font-bold uppercase leading-none tracking-[0.1em] transition-opacity hover:opacity-90 disabled:opacity-60 ${
    saved
      ? "bg-[rgb(26_24_23/0.92)] text-[#fffdfa]"
      : "bg-[rgb(255_253_250/0.92)] text-[#201e1d]"
  }`;
}

/* ── the grid and its states ────────────────────────────────────────────── */

/** Zero-height; the IntersectionObserver target for the next page. */
export const SCROLL_SENTINEL = "h-px";
export const LOADING_MORE = "py-6 font-body text-meta text-ink-3";

/**
 * Skeleton tiles, not a spinner: a spinner collapses the layout, so the page
 * jumps the moment content lands.
 */
export const SKELETON_GRID =
  "grid grid-cols-[repeat(auto-fill,minmax(150px,1fr))] gap-3.5";
export const SKELETON_TILE = "animate-pulse rounded-tile bg-tile";

export const FEED_STATE = "border-t border-rule-soft py-16";
export const FEED_STATE_TITLE =
  "font-heading text-[17px] font-bold leading-tight text-ink";
export const FEED_STATE_BODY = "mt-2 max-w-[60ch] font-body text-[13.5px] text-ink-3";
export const CLEAR_FILTERS_ACTION = `mt-4 inline-block ${ACCENT_TEXT_ACTION}`;
export const RETRY_BUTTON = `mt-4 ${PILL_BUTTON}`;

/* ── the article page ───────────────────────────────────────────────────── */

export const ARTICLE_PAGE = "mx-auto max-w-prose px-gutter pb-[70px] pt-[34px]";
export const ARTICLE_ERROR_PAGE = "mx-auto max-w-prose px-gutter py-24";
export const ARTICLE_ERROR_TITLE = "font-heading text-headline font-extrabold text-ink";
export const BACK_TO_FEED_LINK = `mt-4 inline-block ${ACCENT_TEXT_ACTION} uppercase tracking-[0.06em]`;

export const ARTICLE_BACK_LINK =
  "inline-block border-0 bg-transparent p-0 pb-[26px] font-body text-micro font-semibold uppercase leading-none tracking-[0.13em] text-ink-3 transition-colors hover:text-ink";

export function articleKicker(subjectText: string): string {
  return `mb-4 font-body text-micro font-bold uppercase tracking-[0.14em] ${subjectText}`;
}

export const ARTICLE_TITLE =
  "text-pretty font-heading text-title font-bold leading-[1.04] tracking-[-0.03em] text-ink";
export const ARTICLE_LEDE =
  "mt-5 text-pretty font-body text-[19px] leading-[1.5] text-ink-2";

/** The byline strip — pipe-separated facts, not a table. */
export const ARTICLE_BYLINE =
  "mt-6 flex flex-wrap items-center gap-x-2.5 gap-y-1.5 pb-4 font-body text-[12px] text-ink-3";
export const ARTICLE_BYLINE_STRONG = "font-semibold text-ink";
export const ARTICLE_BYLINE_SEP = "text-ink-4";

/** Verdict, its scope limit and the grade rungs, on one ruled line. */
export const ARTICLE_VERDICT_BAR =
  "mb-[30px] flex flex-wrap items-center gap-x-3 gap-y-2 border-y border-rule-soft py-3.5";
export const ARTICLE_VERDICT_QUALIFIER = "font-body text-[12.5px] text-ink-3";
export const VERDICT_GLOSS_TEXT =
  "-mt-2 mb-7 max-w-[60ch] font-body text-[13px] leading-normal text-ink-3";

/**
 * The retraction notice, above the headline rather than beside the sources.
 *
 * Placed first because it changes how the rest of the page should be read, and
 * a reader who stops after the verdict must still have seen it. It uses the
 * accent ink and a heavy left rule — the same restraint as the rest of the
 * design, one step louder — because this is the only element on a public page
 * that says our own article may be wrong.
 */
export const RETRACTION_BANNER =
  "mb-6 rounded-field border-l-2 border-accent bg-surface px-4 py-3 font-body text-meta text-ink";
export const RETRACTION_BANNER_LABEL =
  "block font-body text-kicker font-bold uppercase tracking-[0.06em] text-accent-ink";
export const RETRACTION_BANNER_TEXT = "mt-1.5 block max-w-[64ch] text-ink-2";

/** Marks the withdrawn paper in the source list, so the banner is actionable. */
export const SOURCE_RETRACTED =
  "mt-1 font-body text-micro font-semibold uppercase tracking-[0.04em] text-accent-ink";

export const ARTICLE_DISCLAIMER =
  "mt-8 max-w-[64ch] font-body text-[11.5px] leading-[1.5] text-ink-4";

/* ── save and share ─────────────────────────────────────────────────────── */

export const ARTICLE_ACTIONS =
  "mt-10 flex flex-wrap items-center gap-2.5 border-t border-rule-soft pt-5";

/** A native select, restyled as a pill. The chevron is drawn beside it. */
export const FOLDER_SELECT_WRAP = "relative inline-flex items-center";
export const FOLDER_SELECT =
  "appearance-none rounded-full border border-rule bg-surface py-3 pl-[18px] pr-10 font-body text-[12px] text-ink";
export const FOLDER_SELECT_CHEVRON =
  "pointer-events-none absolute right-[17px] top-1/2 h-[7px] w-[7px] -translate-y-[70%] rotate-45 border-b-[1.6px] border-r-[1.6px] border-ink-3";

/** The filled action. Ink, not accent — the accent belongs to the console. */
export const PRIMARY_PILL =
  "rounded-full border-0 bg-invert px-5 py-3 font-body text-[11px] font-bold uppercase leading-none tracking-[0.12em] text-invert-fg transition-colors hover:bg-accent-ink disabled:opacity-50";
export const SECONDARY_PILL =
  "rounded-full border border-rule bg-transparent px-5 py-3 font-body text-[11px] font-bold uppercase leading-none tracking-[0.12em] text-ink transition-colors hover:border-ink";

/* ── read more ──────────────────────────────────────────────────────────── */

export const RECS_SECTION = "mt-11 border-t border-rule-soft pt-[22px]";
export const RECS_TITLE =
  "font-heading text-[17px] font-bold tracking-[-0.015em] text-ink";
export const RECS_STANDFIRST = "mt-1 font-body text-[13px] text-ink-3";
export const RECS_GRID = "mt-5 grid grid-cols-1 gap-3.5 sm:grid-cols-3";
export const REC_CARD =
  "block rounded-tile border border-rule-soft bg-surface px-5 pb-[22px] pt-5 transition-colors hover:border-ink";
export const REC_KICKER =
  "mb-2.5 font-body text-[9.5px] font-bold uppercase tracking-[0.13em] text-ink-3";
export const REC_TITLE =
  "text-pretty font-heading text-[15.5px] font-bold leading-[1.22] tracking-[-0.015em] text-ink";

/* ── skeletons ──────────────────────────────────────────────────────────── */

export const ARTICLE_SKELETON_PAGE =
  "mx-auto max-w-prose animate-pulse px-gutter pb-[70px] pt-10";
export const SKELETON_KICKER = "h-5 w-40 rounded-full bg-surface-2";
export const SKELETON_TITLE = "mt-6 h-12 w-4/5 rounded-field bg-surface-2";
export const SKELETON_LEDE = "mt-4 h-6 w-full rounded-field bg-surface-2";
export const SKELETON_LEAD_IMAGE = "mt-8 aspect-[16/9] w-full rounded-tile bg-surface-2";
export const SKELETON_PROSE = "mt-8 space-y-3";
export const SKELETON_PARAGRAPH = "h-24 rounded-field bg-surface-2";

/* ── article prose and citation chips ───────────────────────────────────── */

/**
 * `flow-root` contains the floats a reviewer may have placed. Without it a
 * picture floated beside the last paragraph hangs below the prose and over the
 * disclaimer.
 */
export const PROSE_MEASURE = "flow-root max-w-[64ch]";
export const PROSE_PARAGRAPH =
  "mb-[22px] text-pretty font-body text-prose text-ink-2 last:mb-0";

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
  "absolute left-0 top-[calc(100%+9px)] z-30 block w-[296px] whitespace-normal rounded-field border border-rule-soft bg-surface px-3.5 pb-3.5 pt-[13px] shadow-pop";
export const POPOVER_KICKER =
  "block font-body text-kicker font-bold uppercase text-ink-3";
export const POPOVER_TITLE =
  "mt-2 block font-heading text-[14px] font-semibold leading-[1.35] text-ink";
export const POPOVER_META = "mt-[5px] block font-body text-meta text-ink-3";
export const POPOVER_LINK =
  "mt-2.5 inline-block border-b border-accent pb-0.5 font-body text-micro font-semibold uppercase leading-none tracking-[0.06em] text-accent-ink";
export const POPOVER_UNRESOLVED = "mt-2 block font-body text-meta text-ink-3";

/* ── pictures and video a reviewer added ────────────────────────────────── */

/**
 * Media sits inside the prose measure rather than breaking out of it.
 *
 * A full-bleed image is the magazine move, and this is not a magazine: the
 * column is 64 characters because that is where the evidence reads well, and a
 * picture that escapes it announces itself as the more important thing on the
 * page.
 *
 * The width and the wrap come from `.ew-media` in `styles/youth.css` — the same
 * classes the console's editor uses, which is what makes the reviewer's layout
 * something they can actually see before approving it. Everything inside those
 * classes stops applying below 640px, so a floated picture becomes a full-width
 * block on a phone without anyone deciding that per article.
 */
export function articleFigure(align: MediaAlign): string {
  return `${mediaWrapClass(align)} last:mb-0`;
}

export const ARTICLE_IMAGE = "block h-auto w-full rounded-tile";

/**
 * Video is a facade until it is pressed.
 *
 * An embedded player is roughly a megabyte of Google's JavaScript, executed on
 * every reader of every article that has one, to render a rectangle most of
 * them will not press. The still frame is one image request, and the iframe
 * arrives on the click that asks for it — which is also the click that makes
 * being tracked by YouTube something the reader chose.
 */
export const VIDEO_FRAME =
  "relative block aspect-video w-full overflow-hidden rounded-tile bg-tile";
export const VIDEO_COVER = "absolute inset-0 h-full w-full object-cover";
export const VIDEO_IFRAME = "absolute inset-0 h-full w-full";

/**
 * The play affordance.
 *
 * A solid accent block rather than a translucent overlay: the thumbnail's
 * colours are decided by whoever uploaded the video, and the ink ramp does not
 * carry opacity (see the note in `tailwind.config.ts`), so the only mark that
 * stays legible over an unknown image is an opaque one.
 */
export const VIDEO_PLAY_BUTTON =
  "group absolute inset-0 flex items-center justify-center";
export const VIDEO_PLAY_MARK =
  "flex h-[54px] w-[78px] items-center justify-center rounded-full bg-accent transition-transform group-hover:scale-105";
export const VIDEO_PLAY_TRIANGLE =
  "ml-1 border-y-[11px] border-l-[18px] border-y-transparent border-l-white";
export const VIDEO_CAPTION = "mt-2 font-body text-micro text-ink-3";

/* ── the source list ────────────────────────────────────────────────────── */

/**
 * The sources sit in a panel under the article rather than in a sidebar.
 *
 * The previous design pinned them beside the prose, which answered "what is
 * this resting on?" without scrolling. This one answers it at the end, and the
 * citation chips are what serve the mid-article question — each one opens the
 * paper it points at in place. On a 760px measure a 320px sidebar would leave
 * the prose too narrow to be the thing the page is for.
 */
export const SOURCES_SECTION =
  "mt-9 rounded-panel border border-rule-soft bg-surface-2 px-7 py-[26px]";
export const SOURCES_HEAD = "flex items-baseline justify-between gap-2.5";
export const SOURCES_TITLE = "font-heading text-[14px] font-bold text-ink";
export const SOURCES_COUNT = "font-body text-[11.5px] text-ink-3";
export const SOURCES_EMPTY_NOTE = "mt-3 font-body text-micro text-ink-3";
export const SOURCES_FOOTNOTE = "mt-4 font-body text-micro text-ink-3";
export const SOURCE_LIST = "mt-3.5 flex list-none flex-col gap-2.5 p-0";

/**
 * The row the reader just jumped to is washed with the accent, so the anchor
 * lands somewhere visible. `scroll-mt-[90px]` keeps it clear of the site bar.
 */
export function sourceRow(active: boolean): string {
  return `scroll-mt-[90px] rounded-field px-2 py-1.5 transition-colors ${
    active ? "bg-accent-wash" : "bg-transparent"
  }`;
}

export const SOURCE_ROW_HEAD = "flex items-baseline gap-2.5";

/** The handle, as a pill — the same shape as the chip in the prose. */
export const SOURCE_HANDLE =
  "flex-none rounded-full border border-rule-soft px-2 py-0.5 font-body text-[10.5px] font-bold leading-none tracking-[0.06em] text-accent-ink";
export const SOURCE_TITLE_LINK =
  "text-pretty font-body text-[13px] leading-[1.5] text-ink-2 hover:text-accent-ink";

/**
 * Weak study types are drawn in the accent ink — the one place colour touches
 * evidence, and it flags the *source*, never the verdict.
 */
export function sourceStudyType(weak: boolean): string {
  return `mt-1 font-body text-micro ${weak ? "text-accent-ink" : "text-ink-3"}`;
}

export const SOURCE_META = "font-body text-micro text-ink-3";

/** The study type and journal, indented under the title they describe. */
export const SOURCE_SUBLINE = "ml-[38px]";

export const CITATION_MAP = "mt-4";
export const CITATION_MAP_SUMMARY =
  "cursor-pointer font-body text-micro text-ink-3 hover:text-ink";
export const CITATION_MAP_LIST = "mt-2 flex list-none flex-col gap-1.5 p-0";
export const CITATION_MAP_ITEM = "font-body text-micro text-ink-3";
export const CITATION_MAP_CLAIM = "font-semibold text-ink-2";
export const CITATION_MAP_HANDLE = "underline";
