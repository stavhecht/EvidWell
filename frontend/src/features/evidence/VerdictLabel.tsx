/**
 * Mark plus wording — the pairing every surface uses, so the two can never be
 * shown apart. See VerdictMark.tsx for why the mark alone is not sufficient.
 *
 * The qualifier is the honest half of the verdict: "Supported" on its own is
 * the kind of claim the product exists to check, and "Supported — for strength
 * and lean mass" is the one we can actually stand behind. It is rendered in a
 * lighter ink but at the same size, because it is a scope limit rather than a
 * footnote.
 */

import { VerdictMark } from "./VerdictMark";
import { VERDICT_LABELS } from "./labels";
import { VERDICT_ROW, verdictQualifier, verdictWording } from "./styles";
import type { Verdict } from "@/types/api";

interface Props {
  verdict: Verdict;
  qualifier?: string | null;
  size?: "sm" | "card" | "lg";
  className?: string;
}

export function VerdictLabel({ verdict, qualifier, size = "card", className }: Props) {
  return (
    <span className={`${VERDICT_ROW} ${className ?? ""}`}>
      <VerdictMark verdict={verdict} size={size} />
      <span className={verdictWording(verdict, size)}>{VERDICT_LABELS[verdict]}</span>
      {qualifier ? (
        <span className={verdictQualifier(size)}>{qualifier}</span>
      ) : null}
    </span>
  );
}
