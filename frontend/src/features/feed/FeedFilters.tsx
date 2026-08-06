/**
 * Feed filters: a single button that opens a panel, plus dismissable pills for
 * whatever is currently on.
 *
 * The comp puts the chips behind a disclosure rather than in a permanent row,
 * and that is the right call for this product specifically: a row of verdict
 * chips sitting above the grid reads as a scoreboard legend — "here are the
 * scores, pick one" — which is the framing the content rules exist to avoid.
 * Behind a button they are what they actually are, a way to narrow a list.
 *
 * The active pills stay outside the panel so a filtered feed can never look
 * like an empty one.
 */

import { useEffect, useRef } from "react";
import { ListFilter, X } from "lucide-react";

import { VERDICT_LABELS } from "@/features/evidence/labels";
import { SUBJECT_LABELS, subjectBackground } from "@/features/evidence/subject";
import {
  ACCENT_TEXT_ACTION,
  CHIP_ROW,
  FILTER_BAR,
  FILTER_COUNT,
  FILTER_PANEL,
  FILTER_PANEL_HEAD,
  FILTER_PILL,
  FILTER_SUBJECT_LABEL,
  SECTION_LABEL,
  chipDot,
  filterChip,
  filterToggle,
} from "./styles";
import type { Subject, Verdict } from "@/types/api";

const VERDICT_OPTIONS: (Verdict | null)[] = [
  null,
  "supported",
  "mixed",
  "weak",
  "no_evidence",
];

interface Props {
  verdict: Verdict | null;
  onVerdict: (verdict: Verdict | null) => void;
  subject: Subject | null;
  onSubject: (subject: Subject | null) => void;
  /**
   * Subjects present in the loaded feed. Empty until the API serves the field,
   * and the whole Subject section is hidden while it is — a row of chips that
   * cannot narrow anything is worse than no row. See `subject.ts`.
   */
  availableSubjects: readonly Subject[];
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** e.g. "9 articles · every one editor-approved". */
  count: string;
}

export function FeedFilters({
  verdict,
  onVerdict,
  subject,
  onSubject,
  availableSubjects,
  open,
  onOpenChange,
  count,
}: Props) {
  const container = useRef<HTMLDivElement>(null);
  const filtered = verdict !== null || subject !== null;

  // Dismiss on outside click and on Escape. Both, not either: a disclosure that
  // only closes on Escape strands pointer users, and one that only closes on
  // outside click strands keyboard users.
  useEffect(() => {
    if (!open) return;

    function onPointerDown(event: MouseEvent) {
      if (!container.current?.contains(event.target as Node)) onOpenChange(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") onOpenChange(false);
    }

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, onOpenChange]);

  return (
    <div ref={container} className={FILTER_BAR}>
      <button
        onClick={() => onOpenChange(!open)}
        aria-expanded={open}
        className={filterToggle(filtered)}
      >
        Filter
        <ListFilter size={14} aria-hidden />
      </button>

      {verdict ? (
        <FilterPill label={VERDICT_LABELS[verdict]} onClear={() => onVerdict(null)} />
      ) : null}
      {subject ? (
        <FilterPill label={SUBJECT_LABELS[subject]} onClear={() => onSubject(null)} />
      ) : null}

      <span className={FILTER_COUNT}>{count}</span>

      {open ? (
        <div className={FILTER_PANEL}>
          <div className={FILTER_PANEL_HEAD}>
            <span className={SECTION_LABEL}>Verdict</span>
            <button
              onClick={() => {
                onVerdict(null);
                onSubject(null);
              }}
              className={ACCENT_TEXT_ACTION}
            >
              Clear all
            </button>
          </div>

          <div className={CHIP_ROW}>
            {VERDICT_OPTIONS.map((option) => (
              <Chip
                key={option ?? "all"}
                label={option ? VERDICT_LABELS[option] : "All"}
                active={verdict === option}
                onClick={() => onVerdict(option)}
              />
            ))}
          </div>

          {availableSubjects.length > 0 ? (
            <>
              <span className={FILTER_SUBJECT_LABEL}>Subject</span>
              <div className={CHIP_ROW}>
                <Chip
                  label="All subjects"
                  active={subject === null}
                  onClick={() => onSubject(null)}
                />
                {availableSubjects.map((option) => (
                  <Chip
                    key={option}
                    label={SUBJECT_LABELS[option]}
                    active={subject === option}
                    dot={subjectBackground(option)}
                    onClick={() => onSubject(option)}
                  />
                ))}
              </div>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function Chip({
  label,
  active,
  dot,
  onClick,
}: {
  label: string;
  active: boolean;
  /** Background class for the subject swatch, shown only when inactive. */
  dot?: string;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      aria-pressed={active}
      className={filterChip(active)}
    >
      {dot && !active ? <span aria-hidden className={chipDot(dot)} /> : null}
      {label}
    </button>
  );
}

function FilterPill({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <button
      onClick={onClear}
      aria-label={`Clear the ${label} filter`}
      className={FILTER_PILL}
    >
      {label}
      <X size={13} aria-hidden />
    </button>
  );
}
