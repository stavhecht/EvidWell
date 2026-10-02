/**
 * Subject colour — the only chromatic axis in the product.
 *
 * Colour tells you *which category* an article is filed under, never how it
 * scored. The same categories are the feed's browse filter (`?subject=`).
 * Nothing green, nothing amber, and no hue sits on the verdict axis, so a grid
 * of cards can be chromatic without turning into a traffic light. Every hue
 * clears 5:1 on both grounds and is always redundant with a text label.
 *
 * ── It extends Modernist past its tokens ────────────────────────────────────
 * The bound design system is deliberately mono: one red, no second accent.
 * These nine hues are *not* `--color-accent-*` steps — they are an addition,
 * held to the system's rules (flat fills, 2–4px rules, zero radius, flush left)
 * and kept off every judgment. Red stays the interaction colour, and the
 * supplements hue is drawn from it so the addition still reads as one family.
 */

import type { Subject } from "@/types/api";

/** Drawer, picker and sign-up order. "Other" last: it is the "none of these fit" answer. */
export const SUBJECTS: readonly Subject[] = [
  "fitness",
  "nutrition",
  "supplements",
  "sleep_recovery",
  "lifestyle",
  "preventive_health",
  "general_health",
  "wellness",
  "other",
];

export const SUBJECT_LABELS: Record<Subject, string> = {
  fitness: "Fitness",
  nutrition: "Nutrition",
  supplements: "Supplements",
  sleep_recovery: "Sleep and recovery",
  lifestyle: "Lifestyle",
  preventive_health: "Preventive health",
  general_health: "General health",
  wellness: "Wellness",
  other: "Other",
};

/*
 * Tailwind scans source text for complete class names, so these have to be
 * written out rather than composed (`text-subject-${s}` produces nothing).
 * The upside is that adding a subject is a compile error here until its
 * classes exist, which is the right place to notice. Token names use hyphens
 * (`sleep-recovery`) where the API value uses an underscore.
 */

const TEXT: Record<Subject, string> = {
  fitness: "text-subject-fitness",
  nutrition: "text-subject-nutrition",
  supplements: "text-subject-supplements",
  sleep_recovery: "text-subject-sleep-recovery",
  lifestyle: "text-subject-lifestyle",
  preventive_health: "text-subject-preventive-health",
  general_health: "text-subject-general-health",
  wellness: "text-subject-wellness",
  other: "text-subject-other",
};

const BORDER: Record<Subject, string> = {
  fitness: "border-t-subject-fitness",
  nutrition: "border-t-subject-nutrition",
  supplements: "border-t-subject-supplements",
  sleep_recovery: "border-t-subject-sleep-recovery",
  lifestyle: "border-t-subject-lifestyle",
  preventive_health: "border-t-subject-preventive-health",
  general_health: "border-t-subject-general-health",
  wellness: "border-t-subject-wellness",
  other: "border-t-subject-other",
};

const BORDER_LEFT: Record<Subject, string> = {
  fitness: "border-l-subject-fitness",
  nutrition: "border-l-subject-nutrition",
  supplements: "border-l-subject-supplements",
  sleep_recovery: "border-l-subject-sleep-recovery",
  lifestyle: "border-l-subject-lifestyle",
  preventive_health: "border-l-subject-preventive-health",
  general_health: "border-l-subject-general-health",
  wellness: "border-l-subject-wellness",
  other: "border-l-subject-other",
};

const BACKGROUND: Record<Subject, string> = {
  fitness: "bg-subject-fitness",
  nutrition: "bg-subject-nutrition",
  supplements: "bg-subject-supplements",
  sleep_recovery: "bg-subject-sleep-recovery",
  lifestyle: "bg-subject-lifestyle",
  preventive_health: "bg-subject-preventive-health",
  general_health: "bg-subject-general-health",
  wellness: "bg-subject-wellness",
  other: "bg-subject-other",
};

/** Kicker text above a headline. Falls back to ink-3, never to a stand-in hue. */
export function subjectText(subject: Subject | null | undefined): string {
  return subject ? TEXT[subject] : "text-ink-3";
}

/** The card's top rule and the article's opening rule. */
export function subjectBorderTop(subject: Subject | null | undefined): string {
  return subject ? BORDER[subject] : "border-t-rule";
}

/** The queue row's left rule. */
export function subjectBorderLeft(subject: Subject | null | undefined): string {
  return subject ? BORDER_LEFT[subject] : "border-l-rule";
}

/** The dot on an inactive subject filter chip. */
export function subjectBackground(subject: Subject | null | undefined): string {
  return subject ? BACKGROUND[subject] : "bg-ink";
}

export function subjectLabel(subject: Subject | null | undefined): string | null {
  return subject ? SUBJECT_LABELS[subject] : null;
}
