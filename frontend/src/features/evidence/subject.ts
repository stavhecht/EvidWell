/**
 * Subject colour — the only chromatic axis in the product.
 *
 * Colour tells you *what kind of thing* is being assessed, never how it scored.
 * Nothing green, nothing amber, and no hue sits on the verdict axis, so a grid
 * of cards can be chromatic without turning into a traffic light. Every hue
 * clears 5:1 on both grounds and is always redundant with a text label.
 *
 * ── Not served yet ──────────────────────────────────────────────────────────
 * `subject` does not exist on the backend article model. It cannot be derived
 * from `product`, which is free text, so shipping the coloured rule needs an
 * editor-set enum first. Until then every helper here degrades to ink and the
 * feed simply reads mono — which is the design system's resting state, not a
 * broken one. Nothing below will start rendering colour by accident; it starts
 * when the API starts sending the field.
 *
 * ── It extends Modernist past its tokens ────────────────────────────────────
 * The bound design system is deliberately mono: one red, no second accent.
 * These five hues are *not* `--color-accent-*` steps — they are an addition,
 * held to the system's rules (flat fills, 2–4px rules, zero radius, flush left)
 * and kept off every judgment. Red stays the interaction colour, and the
 * supplement hue is drawn from it so the addition still reads as one family.
 */

import type { Subject } from "@/types/api";

export const SUBJECTS: readonly Subject[] = [
  "supplement",
  "device",
  "protocol",
  "food",
  "topical",
];

export const SUBJECT_LABELS: Record<Subject, string> = {
  supplement: "Supplement",
  device: "Device",
  protocol: "Protocol",
  food: "Food & drink",
  topical: "Topical",
};

/*
 * Tailwind scans source text for complete class names, so these have to be
 * written out rather than composed (`text-subject-${s}` produces nothing).
 * The upside is that adding a subject is a compile error here until its
 * classes exist, which is the right place to notice.
 */

const TEXT: Record<Subject, string> = {
  supplement: "text-subject-supplement",
  device: "text-subject-device",
  protocol: "text-subject-protocol",
  food: "text-subject-food",
  topical: "text-subject-topical",
};

const BORDER: Record<Subject, string> = {
  supplement: "border-t-subject-supplement",
  device: "border-t-subject-device",
  protocol: "border-t-subject-protocol",
  food: "border-t-subject-food",
  topical: "border-t-subject-topical",
};

const BORDER_LEFT: Record<Subject, string> = {
  supplement: "border-l-subject-supplement",
  device: "border-l-subject-device",
  protocol: "border-l-subject-protocol",
  food: "border-l-subject-food",
  topical: "border-l-subject-topical",
};

const BACKGROUND: Record<Subject, string> = {
  supplement: "bg-subject-supplement",
  device: "bg-subject-device",
  protocol: "bg-subject-protocol",
  food: "bg-subject-food",
  topical: "bg-subject-topical",
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
