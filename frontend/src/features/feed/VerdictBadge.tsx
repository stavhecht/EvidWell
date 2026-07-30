/**
 * The verdict badge — the single most important pixel in the product.
 *
 * It is the first thing a reader parses on a card and it is what they will
 * remember. Two rules follow:
 *
 * 1. **Never colour-only.** The label is always rendered as text alongside the
 *    colour. Colour-blind readers and greyscale screenshots both have to work,
 *    and a verdict conveyed only by hue fails silently for both.
 * 2. **`no_evidence` is neutral, not alarming.** "We looked and found nothing"
 *    is an honest, useful result — styling it like a warning implies the
 *    product is bad rather than unstudied, which is exactly the brand-attacking
 *    framing the content rules forbid.
 */

import type { Verdict } from "@/types/api";

const LABELS: Record<Verdict, string> = {
  supported: "Supported by evidence",
  mixed: "Mixed evidence",
  weak: "Weak evidence",
  no_evidence: "No evidence found",
};

const STYLES: Record<Verdict, string> = {
  supported: "bg-green-50 text-verdict-supported ring-green-600/20",
  mixed: "bg-amber-50 text-verdict-mixed ring-amber-600/20",
  weak: "bg-orange-50 text-verdict-weak ring-orange-600/20",
  // Deliberately neutral stone, not red. See rule 2 above.
  no_evidence: "bg-stone-100 text-verdict-none ring-stone-500/20",
};

interface Props {
  verdict: Verdict;
  qualifier?: string | null;
  size?: "sm" | "md";
}

export function VerdictBadge({ verdict, qualifier, size = "md" }: Props) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full ring-1 ring-inset font-medium ${
        STYLES[verdict]
      } ${size === "sm" ? "px-2 py-0.5 text-xs" : "px-2.5 py-1 text-sm"}`}
    >
      {LABELS[verdict]}
      {qualifier ? (
        <span className="font-normal opacity-80">— {qualifier}</span>
      ) : null}
    </span>
  );
}
