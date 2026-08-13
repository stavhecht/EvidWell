/**
 * Names for the console's styling — queue, review screen, sources panel, login.
 *
 * The shared control shapes stay in `controls.ts`: `FIELD`, `PRIMARY`,
 * `SECONDARY` and `SECTION_LABEL` are the pieces whose *consistency* is a
 * safety property, and they are composed here rather than restated. Everything
 * in this file is the surrounding layout, per screen, in reading order.
 *
 * The accent is load-bearing on this side of the product: it is the only fill
 * in the console, so the one button that publishes is unmistakable. Anything
 * new that reaches for `bg-accent` is competing with Approve.
 */

import { FIELD, PRIMARY, SECONDARY } from "./controls";

/* ── shared console chrome ──────────────────────────────────────────────── */

/** Wider than the public measure — the queue is a working surface, not prose. */
export const CONSOLE_PAGE = "mx-auto max-w-console px-gutter pb-[90px]";

export const SIGNED_IN_AS =
  "inline-flex items-baseline gap-[7px] font-body text-[12px] leading-normal text-ink-3";
export const SIGN_OUT_ACTION =
  "border-b border-accent pb-px font-body text-[12px] font-semibold leading-normal text-accent-ink";

/* ── the review queue ───────────────────────────────────────────────────── */

export const QUEUE_HEADER =
  "flex flex-wrap items-baseline justify-between gap-4 border-b-2 border-rule pb-4 pt-[38px]";
export const QUEUE_TITLE = "font-heading text-headline font-extrabold text-ink";
export const QUEUE_STANDFIRST = "mt-[9px] font-body text-field leading-normal text-ink-3";
export const QUEUE_MESSAGE = "py-8 font-body text-meta text-ink-3";
export const QUEUE_LIST = "list-none p-0";
export const QUEUE_FOOTNOTE = "mt-5 font-body text-[12px] leading-normal text-ink-3";

export const TAB_BAR = "mt-[22px] flex gap-0.5 border-b border-rule-soft";

/** `-mb-px` pulls the active tab's rule over the bar's, so they read as one. */
export function queueTab(current: boolean): string {
  return `-mb-px border-b-2 px-[13px] py-[11px] font-body text-[12px] font-semibold leading-none tracking-[0.05em] transition-colors ${
    current ? "border-ink text-ink" : "border-transparent text-ink-3 hover:text-ink"
  }`;
}

/** Only the open tab's count is known — the queue is fetched one status at a time. */
export const TAB_COUNT = "ml-1.5 font-normal text-ink-4";

/**
 * The row carries the verdict mark and the grade bar at once, because triage is
 * really the question "which of these is a confident verdict on thin evidence?"
 * — and that is a question about the pair.
 */
export function queueRow(subjectBorderLeft: string): string {
  return `grid grid-cols-1 items-start gap-4 border-b border-l-[3px] border-rule-soft py-[18px] pl-[15px] pr-0.5 sm:grid-cols-[minmax(0,1fr)_200px] sm:gap-[26px] ${subjectBorderLeft}`;
}

export const QUEUE_ROW_LINK = "min-w-0 transition-colors hover:text-ink";
export const QUEUE_ROW_SIGNALS = "flex flex-wrap items-center gap-[9px]";
export const QUEUE_VERDICT_WORDING =
  "font-body text-label font-semibold uppercase leading-none tracking-[0.09em] text-ink-3";
export const QUEUE_VALIDATION_BADGE = "font-body text-micro leading-none text-ink-3";

/** Outlined, never filled: a prompt to check the sources, not a rejection. */
export const WEAK_EVIDENCE_FLAG =
  "border border-accent px-[7px] py-1 font-body text-label font-semibold normal-case leading-none tracking-normal text-accent-ink";

export const QUEUE_ROW_HEADLINE =
  "mt-[9px] text-pretty font-heading text-row font-semibold text-ink";
export const QUEUE_ROW_META = "mt-1 font-body text-meta text-ink-3";
export const QUEUE_ROW_ERROR = "mt-1 font-body text-micro text-accent-ink";
export const QUEUE_ROW_ASIDE = "flex items-start justify-between gap-3";
export const QUEUE_ROW_GRADE = "flex flex-col items-start gap-[7px]";
export const QUEUE_ROW_GRADE_LABEL = "font-body text-micro text-ink-3";

/** Discard is a rejection, not a delete — there is no DELETE on articles. */
export const DISCARD_BUTTON =
  "shrink-0 border border-rule-soft p-1.5 text-ink-3 transition-colors hover:border-ink hover:text-accent-ink disabled:opacity-45";

/* ── queue a new draft ──────────────────────────────────────────────────── */

export const NEW_RUN_FORM = "mt-5 border border-rule-soft bg-surface p-4";
export const NEW_RUN_ROW = "flex flex-wrap gap-2";
export const NEW_RUN_TOPIC_FIELD = `${FIELD} flex-1 basis-64`;
export const NEW_RUN_BLURB_FIELD = `${FIELD} mt-2 resize-y`;
export const NEW_RUN_CONFIRMATION = "mt-2 font-body text-micro text-ink-3";
export const NEW_RUN_ERROR = "mt-2 font-body text-micro text-accent-ink";

/* ── the review screen ──────────────────────────────────────────────────── */

export const REVIEW_PAGE = "mx-auto max-w-page px-gutter pb-24";
export const REVIEW_TOP_BAR =
  "flex flex-wrap items-center justify-between gap-3.5 pt-[22px]";
export const BACK_TO_QUEUE =
  "font-body text-micro font-semibold uppercase leading-none tracking-[0.11em] text-ink-3 hover:text-ink";

/**
 * Editor left, evidence right. Side by side is the requirement — the reviewer
 * cross-checks one against the other continuously — and the page scrolls with
 * the evidence column pinned rather than two panes scrolling independently, so
 * the wheel is never trapped in whichever pane the cursor is over.
 */
export const REVIEW_COLUMNS =
  "mt-4 grid grid-cols-1 items-start gap-12 lg:grid-cols-[minmax(0,1fr)_330px]";
export const REVIEW_BODY_COLUMN = "min-w-0";
export const REVIEW_SIDEBAR = "min-w-0 lg:sticky lg:top-[86px]";

export const DRAFT_STATUS_ROW =
  "flex flex-wrap items-center gap-2.5 border-t-2 border-rule pt-3.5";
export const STATUS_DIVIDER = "h-3 w-px bg-rule-soft";
export const DRAFT_VERDICT_WORDING =
  "font-body text-label font-semibold uppercase leading-none tracking-[0.09em] text-ink-2";
export const DRAFT_META = "font-body text-meta text-ink-3";

/** Read-only: the headline is not part of the autosave contract. */
export const DRAFT_HEADLINE =
  "mt-[18px] border-b-2 border-rule-soft pb-3 font-heading text-subhead font-extrabold text-ink";
export const EDITOR_SLOT = "mt-[18px]";

export const SIDEBAR_SOURCES = "mt-6";

/**
 * The decision controls sit at the bottom of the evidence column, under what
 * they follow from. Putting Approve in the top bar — the reflex — places it
 * where a reviewer's hand rests *before* they have read anything.
 */
export const DECISION_BLOCK = "mt-6 border-t-2 border-rule pt-[13px]";
export const APPROVE_BUTTON = `${PRIMARY} mt-3 w-full`;
export const REJECT_REASON_FIELD = `${FIELD} mt-2.5 py-2.5 text-meta`;
export const REJECT_BUTTON = `${SECONDARY} mt-2 w-full`;
export const REASON_MISSING_ALERT = "mt-2.5 font-body text-micro text-accent-ink";

/** Carries the server's detail verbatim — a 409 names which handles broke. */
export const ACTION_ERROR_ALERT =
  "mt-2.5 border-l-2 border-accent bg-accent-wash px-[11px] py-[9px] font-body text-micro font-semibold leading-[1.45] text-accent-ink";
export const DECISION_NOTE = "mt-[11px] font-body text-micro text-ink-3";

/* ── the sources panel ──────────────────────────────────────────────────── */

export const PANEL_BLOCK = "border-t-2 border-rule pt-[13px]";
export const CLAIM_GROUP = "mt-3.5";
export const CLAIM_HEADING = "font-body text-micro font-semibold text-ink-2";
export const PANEL_SOURCE_LIST = "mt-2.5 flex list-none flex-col gap-3 p-0";

/**
 * Two independent signals on one row. `focused` washes the row the reviewer
 * just clicked through to; uncited sources are dimmed but never hidden —
 * what the model left out is the failure mode human review exists to catch.
 */
export function panelSourceRow(focused: boolean, wasCited: boolean): string {
  return `border-l-2 pl-2.5 transition-colors ${
    focused ? "border-l-accent bg-accent-wash" : "border-l-transparent"
  } ${wasCited ? "" : "opacity-45"}`;
}

export const PANEL_SOURCE_HEAD = "flex items-baseline gap-2";
export const PANEL_SOURCE_HANDLE =
  "flex-none font-body text-label font-semibold leading-[1.3] tracking-[0.06em] text-ink-3";
export const PANEL_SOURCE_TITLE_LINK =
  "text-pretty font-body text-meta font-semibold leading-[1.35] text-ink hover:text-accent-ink";

export function panelSourceStudyType(weak: boolean): string {
  return `ml-[26px] mt-[3px] font-body text-micro ${
    weak ? "text-accent-ink" : "text-ink-3"
  }`;
}

export const PANEL_SOURCE_META = "ml-[26px] font-body text-micro text-ink-3";

/* ── the validation summary ─────────────────────────────────────────────── */

export const VALIDATION_HEADLINE =
  "mt-2.5 font-heading text-[14px] font-semibold leading-tight text-ink";

/** A failing report is a list of what broke, never a colour. */
export const VALIDATION_FAILURES = "mt-2 flex list-none flex-col gap-1.5 p-0";
export const VALIDATION_FAILURE = "font-body text-micro text-accent-ink";

export const PANEL_GRADE_BAR = "mt-3";
export const PANEL_GRADE_NOTE = "mt-[9px] font-body text-[12px] leading-normal text-ink-3";
export const WEAK_EVIDENCE_WARNING =
  "mt-[11px] border-l-2 border-accent bg-accent-wash px-[11px] py-[9px] font-body text-[12px] font-semibold leading-[1.45] text-accent-ink";

/* ── login ──────────────────────────────────────────────────────────────── */

export const LOGIN_PAGE =
  "mx-auto flex max-w-page justify-center px-gutter pb-[120px] pt-16";
export const LOGIN_COLUMN = "w-full max-w-[400px]";
export const LOGIN_INTRO = "border-t-2 border-rule pt-[15px]";
export const LOGIN_TITLE = "font-heading text-subhead font-extrabold text-ink";
export const LOGIN_STANDFIRST = "mt-[11px] font-body text-field leading-normal text-ink-3";
export const LOGIN_FORM = "mt-[22px] border border-rule-soft bg-surface p-5";
export const LOGIN_FIELD = `${FIELD} mt-2`;
export const LOGIN_FIELD_GROUP = "mt-4";
export const LOGIN_ERROR = "mt-3 font-body text-meta text-accent-ink";
export const LOGIN_SUBMIT = `${PRIMARY} mt-[18px] w-full`;
export const FIELD_LABEL =
  "block font-body text-kicker font-semibold uppercase tracking-[0.12em] text-ink-3";

/* ── the editor ─────────────────────────────────────────────────────────── */

/**
 * Passed to TipTap as a raw attribute rather than rendered by React, so the
 * arbitrary-variant selectors are how paragraph spacing gets set at all.
 *
 * The `.ProseMirror-selectednode` rule is what makes an image or a video block
 * feel like an object. They are atoms: a click selects the whole thing and
 * Backspace removes it — but only if the reviewer can see that it is selected,
 * and a block with no visible selection state reads as an un-deletable
 * fixture.
 *
 * `flow-root` makes the surface contain its floats. Without it a picture
 * floated beside the last beat hangs out of the bottom of the editing panel
 * and over whatever follows it.
 */
export const EDITOR_PROSE =
  "flow-root font-body text-[15px] leading-[1.72] text-ink focus:outline-none [&_p]:mb-4 [&_p:last-child]:mb-0 [&_.ProseMirror-selectednode]:outline [&_.ProseMirror-selectednode]:outline-2 [&_.ProseMirror-selectednode]:outline-offset-2 [&_.ProseMirror-selectednode]:outline-accent";

/** `-mt-px` collapses the toolbar's bottom rule into the surface's top one. */
export const EDITOR_SURFACE = "-mt-px border border-rule-soft bg-surface p-[18px]";
export const EDITOR_STATUS_ROW =
  "mt-3 flex flex-wrap items-baseline justify-between gap-3";
export const EDITOR_NOTE = "font-body text-micro text-ink-3";

export const TOOLBAR =
  "flex flex-wrap items-center gap-0.5 border border-rule-soft bg-surface p-1.5";

export function toolbarButton(active: boolean): string {
  return `px-2.5 py-[7px] font-body text-[12px] font-semibold leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-45 ${
    active ? "bg-ink text-ground" : "text-ink-2 hover:bg-ground hover:text-ink"
  }`;
}

/** Separates the two marks from the two things that insert a block. */
export const TOOLBAR_DIVIDER = "mx-1.5 h-4 w-px bg-rule-soft";

/* ── media in the editor ────────────────────────────────────────────────── */

/**
 * The frame inside a media node view.
 *
 * The *layout* — the float and the width — is not here. TipTap's React
 * renderer wraps a node view in an element of its own, and that element is the
 * one sitting in the editor's flow, so it is the one that has to float. It
 * gets `mediaWrapClass()` from `lib/media.ts` through the node's `attrs`
 * option (see `MediaNodes.ts`), which is the same class the published article
 * uses.
 *
 * What is left for the figure is the part that only exists while editing:
 * `relative` to anchor the control bar and the resize grip, and a named group
 * so the grip can appear on hover.
 */
export const MEDIA_FRAME = "group/media relative block";

/**
 * Unstyled beyond a hairline. The reviewer is checking that the right picture
 * is in the right place, and a treatment the published page does not share
 * would be a lie about what they are approving.
 */
export const EDITOR_IMAGE = "block h-auto w-full border border-rule-soft";

/**
 * The video is a 16:9 still, not a player.
 *
 * The frame matches the shape that publishes — a reviewer sizing a video needs
 * to see the rectangle a reader will get, and the old thumbnail-and-label card
 * was a different shape at a fixed size. A live iframe is still out: inside a
 * contenteditable it swallows every click, so the node could never be selected
 * or deleted, and it would load YouTube's player to tell a reviewer something
 * a still already tells them.
 */
export const EDITOR_VIDEO_FRAME =
  "relative block aspect-video w-full border border-rule-soft bg-ground";
export const EDITOR_VIDEO_STILL = "absolute inset-0 h-full w-full object-cover";

/** Sits over the still so the id stays checkable at any size. */
export const EDITOR_VIDEO_OVERLAY =
  "absolute inset-x-0 bottom-0 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 bg-ink px-2.5 py-1.5";
export const EDITOR_VIDEO_KICKER =
  "font-body text-kicker font-semibold uppercase leading-none tracking-[0.13em] text-ground";
export const EDITOR_VIDEO_ID =
  "truncate font-body text-micro font-semibold leading-none text-ground";

/* ── the controls on a selected picture or video ────────────────────────── */

/**
 * A bar over the selected block, not more buttons in the main toolbar.
 *
 * The controls are where the reviewer's attention already is, and the toolbar
 * stays as short as it was — six permanently-inert buttons would be worse than
 * no buttons. `-top-*` lifts it clear of the frame; it is `absolute` so it
 * never changes the size the reviewer is trying to judge.
 */
export const MEDIA_CONTROLS =
  "absolute -top-[38px] left-0 z-20 flex items-center gap-0.5 whitespace-nowrap border border-rule-soft bg-surface px-1 py-1 shadow-panel";
export const MEDIA_CONTROL_DIVIDER = "mx-1 h-3.5 w-px bg-rule-soft";

export function mediaControlButton(active: boolean): string {
  return `px-2 py-1.5 font-body text-[11px] font-semibold leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
    active ? "bg-ink text-ground" : "text-ink-2 hover:bg-ground hover:text-ink"
  }`;
}

/** The live percentage. Tabular so the bar does not twitch as it is dragged. */
export const MEDIA_WIDTH_READOUT =
  "min-w-[38px] text-center font-body text-[11px] font-semibold tabular-nums leading-none text-ink-3";

/** Destructive, so it is the accent — the same red as everything that acts. */
export const MEDIA_REMOVE_BUTTON =
  "px-2 py-1.5 font-body text-[11px] font-semibold leading-none text-accent-ink transition-colors hover:bg-accent-wash";

/**
 * The corner grip.
 *
 * Only on hover or selection: a permanent handle on every picture turns the
 * draft into a page of controls. `cursor-nwse-resize` is what tells a reviewer
 * this is a resize rather than a second drag target — dragging the picture
 * itself moves it between paragraphs.
 */
export const MEDIA_RESIZE_HANDLE =
  "absolute -bottom-1.5 -right-1.5 z-20 h-4 w-4 cursor-nwse-resize border border-ground bg-accent opacity-0 transition-opacity group-hover/media:opacity-100 focus-visible:opacity-100";
export const MEDIA_RESIZE_HANDLE_VISIBLE = "opacity-100";

/** A failed upload, next to the toolbar that started it. */
export const MEDIA_ERROR =
  "mt-2 font-body text-micro font-semibold text-accent-ink";

/** A failed save is the one editor state that blocks Approve, so it is bolder. */
export function saveIndicator(failed: boolean): string {
  return `font-body text-micro ${failed ? "font-semibold text-accent-ink" : "text-ink-3"}`;
}
