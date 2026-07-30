/**
 * The sources panel — "fast source access", the core review requirement.
 *
 * The reviewer's job is to answer "does this article's evidence actually say
 * what it claims?" as fast as possible. Four design consequences:
 *
 * 1. **Grouped by claim, not by source.** The reviewer reads a claim in the
 *    editor and needs its backing evidence next to it. A flat source list makes
 *    them do the join in their head.
 * 2. **Study type and year on every row.** These are what caps the verdict, so
 *    they belong at a glance, not behind a click.
 * 3. **Weak evidence flagged inline.** A confident verdict resting on two
 *    cell-culture studies has to be visible without opening anything.
 * 4. **Uncited sources shown, greyed, not hidden.** What the model chose to
 *    leave out is the failure mode human review exists to catch. If retrieval
 *    surfaced a contradicting trial and the draft ignored it, this panel is the
 *    only place that becomes visible.
 *
 * Everything here comes from one request (`article_sources` joined to
 * `sources`, server-side) — no client-side resolution, no waterfall.
 */

import { useEffect, useRef } from "react";
import { AlertTriangle, ExternalLink } from "lucide-react";

import type { ReviewSource, StudyType, ValidationReport } from "@/types/api";

const STUDY_TYPE_LABELS: Record<StudyType, string> = {
  meta_analysis: "Meta-analysis",
  systematic_review: "Systematic review",
  rct: "RCT",
  observational: "Observational",
  case_report: "Case report",
  animal: "Animal",
  in_vitro: "In-vitro",
  unknown: "Unclear",
};

interface Props {
  sources: ReviewSource[];
  validation: ValidationReport;
  /** Set when the reviewer clicks a citation chip in the editor. */
  focusedHandle?: string | null;
}

export function SourcesPanel({ sources, validation, focusedHandle }: Props) {
  const byClaim = groupByClaim(sources);

  return (
    <aside className="flex h-full flex-col overflow-y-auto border-l border-stone-200 bg-stone-50">
      <ValidationBadge report={validation} />

      {Object.entries(byClaim).map(([claim, claimSources]) => (
        <section key={claim} className="border-b border-stone-200 px-4 py-3">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-stone-500">
            {claim}
          </h3>
          <ul className="mt-2 space-y-2">
            {claimSources.map((source) => (
              <SourceRow
                key={source.sourceId + claim}
                source={source}
                focused={focusedHandle === source.citationHandle}
              />
            ))}
          </ul>
        </section>
      ))}
    </aside>
  );
}

function SourceRow({ source, focused }: { source: ReviewSource; focused: boolean }) {
  const ref = useRef<HTMLLIElement>(null);

  useEffect(() => {
    if (focused) ref.current?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [focused]);

  return (
    <li
      ref={ref}
      className={`rounded-lg border p-2.5 text-sm transition ${
        focused ? "ring-2 ring-stone-400 " : ""
      }${
        source.wasCited
          ? "border-stone-200 bg-white"
          : // Retrieved but not cited. Visible, de-emphasised — the reviewer
            // needs to notice a relevant paper the draft skipped.
            "border-dashed border-stone-300 bg-transparent opacity-60"
      }`}
    >
      <div className="flex items-start justify-between gap-2">
        <span className="font-mono text-xs text-stone-500">{source.citationHandle}</span>
        {source.isWeakEvidence ? (
          <span
            className="flex items-center gap-1 text-xs text-verdict-weak"
            title="Weak study type — check that the verdict does not overstate this"
          >
            <AlertTriangle size={12} aria-hidden />
            weak
          </span>
        ) : null}
      </div>

      <a
        href={source.url}
        target="_blank"
        rel="noreferrer noopener"
        className="mt-0.5 block font-medium text-stone-900 hover:underline"
      >
        {source.title}
        <ExternalLink size={12} className="ml-1 inline align-baseline" aria-hidden />
      </a>

      <p className="mt-0.5 text-xs text-stone-500">
        {STUDY_TYPE_LABELS[source.studyType]}
        {source.journal ? ` · ${source.journal}` : ""}
        {source.year ? ` · ${source.year}` : ""}
        {source.citationCount != null ? ` · ${source.citationCount} citations` : ""}
        {!source.wasCited ? " · retrieved, not cited" : ""}
      </p>
    </li>
  );
}

/** "4/4 citations resolve". Computed server-side at draft time. */
function ValidationBadge({ report }: { report: ValidationReport }) {
  return (
    <div
      className={`sticky top-0 z-10 border-b px-4 py-3 text-sm font-medium ${
        report.passed
          ? "border-green-200 bg-green-50 text-verdict-supported"
          : "border-orange-200 bg-orange-50 text-verdict-weak"
      }`}
    >
      {report.citationsResolved}/{report.citationsTotal} citations resolve
      {!report.passed ? (
        <ul className="mt-1 list-disc pl-4 text-xs font-normal">
          {report.failures.map((failure, index) => (
            <li key={index}>{failure.message}</li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/**
 * Group by claim; cited sources first, then by handle number.
 *
 * Cited-first because those are what the reviewer is verifying. The uncited
 * ones still appear below, which is the point — but they should not push the
 * cited evidence out of view.
 */
function groupByClaim(sources: ReviewSource[]): Record<string, ReviewSource[]> {
  const grouped = sources.reduce<Record<string, ReviewSource[]>>((acc, source) => {
    (acc[source.claim] ??= []).push(source);
    return acc;
  }, {});

  for (const claim of Object.keys(grouped)) {
    grouped[claim]!.sort((a, b) => {
      if (a.wasCited !== b.wasCited) return a.wasCited ? -1 : 1;
      return handleNumber(a.citationHandle) - handleNumber(b.citationHandle);
    });
  }
  return grouped;
}

/** Sorts S2 before S10, which lexicographic ordering would not. */
function handleNumber(handle: string): number {
  const digits = Number.parseInt(handle.slice(1), 10);
  return Number.isNaN(digits) ? 0 : digits;
}
