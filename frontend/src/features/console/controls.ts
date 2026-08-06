/**
 * The console's three control styles, in one place.
 *
 * Small enough to inline, and that is exactly why they are not: the login form,
 * the queue and the review screen all render an approve-shaped button and a
 * reject-shaped button, and when those drift the console stops teaching which
 * action is which. Consistency here is a safety property, not tidiness — this
 * is the surface where one button publishes.
 */

/**
 * A text field. Inputs sit on the *ground* inside a surface panel — inverted
 * from the usual, so the editable region is the recess rather than the raised
 * thing. It is the strongest available cue for "this one is yours to change"
 * in a system with no corner radius to lean on.
 */
export const FIELD =
  "w-full border border-rule-soft bg-ground px-3 py-[11px] font-body text-[14px] leading-tight text-ink placeholder:text-ink-4";

/**
 * The primary action. Flush-left label, per the design system: a button wider
 * than its text starts the text at the left padding edge, never centred.
 *
 * Solid accent is reserved for it. Red is the interaction colour and it is the
 * only fill in the console, so the one button that publishes is unmistakable.
 */
export const PRIMARY =
  "border-0 bg-accent px-3.5 py-[13px] text-left font-body text-[12px] font-semibold uppercase leading-none tracking-[0.1em] text-white transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-45";

/** The paired secondary or destructive action. Outlined, never filled. */
export const SECONDARY =
  "border border-rule-soft bg-transparent px-3.5 py-3 text-left font-body text-[12px] font-semibold uppercase leading-none tracking-[0.1em] text-ink-2 transition-colors hover:border-ink hover:text-ink disabled:cursor-not-allowed disabled:opacity-45";

/** A small caps label above a field or a sidebar section. */
export const SECTION_LABEL =
  "block font-body text-label-sm font-semibold uppercase text-ink-3";
