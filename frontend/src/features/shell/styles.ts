/**
 * Names for the shared chrome's styling.
 *
 * The site bar is the one surface both route groups render, so its measurements
 * are the ones that must not drift: the header height, the page measure and the
 * gutter are what every route below aligns to.
 *
 * `ROUTE_MESSAGE` lives here rather than in either feature because all three
 * route-level waiting states — the lazy console boundary, the auth gate and the
 * review screen — render the same centred paragraph. They looked like three
 * coincidentally-identical strings; they are one decision about how a route
 * says "not yet".
 */

/** Sticky, and above the filter panel's z-30 so the bar is never overlapped. */
export const SITE_HEADER = "sticky top-0 z-40 border-b-2 border-rule bg-ground";

/** The bar's inner measure — same page width and gutter every route uses. */
export const HEADER_BAR =
  "mx-auto flex h-header max-w-page items-center justify-between gap-8 px-gutter";

export const WORDMARK = "flex flex-none flex-col gap-1";

export const WORDMARK_TYPE =
  "font-heading text-[18px] font-extrabold uppercase leading-none tracking-[0.02em] text-ink";

/**
 * The spectrum rule: the subject palette compressed to 34×3px under the type.
 *
 * The four bands are written out rather than mapped over `SUBJECTS`, because
 * Tailwind scans for complete class names — a composed `bg-subject-${s}`
 * produces no CSS at all. Same constraint, and same reasoning, as
 * `evidence/subject.ts`.
 */
export const SPECTRUM_RULE = "flex h-[3px] w-[34px]";
export const SPECTRUM_BAND_SUPPLEMENT = "flex-1 bg-subject-supplement";
export const SPECTRUM_BAND_DEVICE = "flex-1 bg-subject-device";
export const SPECTRUM_BAND_PROTOCOL = "flex-1 bg-subject-protocol";
export const SPECTRUM_BAND_FOOD = "flex-1 bg-subject-food";

export const PRIMARY_NAV = "ml-auto flex items-center gap-[22px]";

const NAV_LINK =
  "border-b-2 py-[7px] font-body text-micro font-semibold uppercase leading-none tracking-[0.11em] transition-colors";

/**
 * The current route is marked by an accent underline *and* a step up in ink —
 * two channels, because the underline alone is a 2px cue and the ink shift
 * alone is a contrast judgment.
 */
export function navLink(isActive: boolean): string {
  return `${NAV_LINK} ${
    isActive ? "border-accent text-ink" : "border-transparent text-ink-3 hover:text-ink"
  }`;
}

export const THEME_TOGGLE =
  "flex-none border border-rule-soft px-2.5 py-[7px] font-body text-label font-semibold uppercase leading-none text-ink-3 transition-colors hover:border-ink-3 hover:text-ink";

/** A route-level waiting or empty state, on the page measure. */
export const ROUTE_MESSAGE =
  "mx-auto max-w-page px-gutter py-16 font-body text-meta text-ink-3";

/** The same, when the route failed rather than is still arriving. */
export const ROUTE_ERROR_MESSAGE =
  "mx-auto max-w-page px-gutter py-16 font-body text-meta text-accent-ink";
