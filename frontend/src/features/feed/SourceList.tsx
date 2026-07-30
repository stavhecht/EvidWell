/**
 * The citation list under a public article.
 *
 * Shows study type on every entry. That is not decoration: it is how a reader
 * calibrates a verdict for themselves rather than taking ours on trust, which
 * is the whole proposition of the product.
 *
 * Each entry carries `id="source-S1"` so the superscript markers in the body
 * can anchor to it.
 */

import type { Citation, Source, StudyType } from "@/types/api";

export const STUDY_TYPE_LABELS: Record<StudyType, string> = {
  meta_analysis: "Meta-analysis",
  systematic_review: "Systematic review",
  rct: "Randomised controlled trial",
  observational: "Observational study",
  case_report: "Case report",
  animal: "Animal study",
  in_vitro: "In-vitro study",
  unknown: "Study type unclear",
};

/** Everything below an observational study. Mirrors `is_weak_evidence` server-side. */
const WEAK: ReadonlySet<StudyType> = new Set<StudyType>([
  "in_vitro",
  "animal",
  "case_report",
  "unknown",
]);

interface Props {
  sources: Source[];
  citations: Citation[];
}

export function SourceList({ sources, citations }: Props) {
  if (sources.length === 0) {
    return (
      <section className="mt-8 border-t border-stone-200 pt-4">
        <p className="text-sm text-stone-500">
          No studies were found that test this claim directly.
        </p>
      </section>
    );
  }

  return (
    <section className="mt-8 border-t border-stone-200 pt-6">
      <h2 className="text-sm font-semibold uppercase tracking-wide text-stone-500">
        Sources
      </h2>

      <ol className="mt-3 space-y-3">
        {sources.map((source) => (
          <li
            key={source.citationHandle}
            id={`source-${source.citationHandle}`}
            className="scroll-mt-6"
          >
            <div className="flex items-baseline gap-2">
              <span className="font-mono text-xs text-stone-400">
                {source.citationHandle}
              </span>
              <a
                href={source.url}
                target="_blank"
                rel="noreferrer noopener"
                className="text-sm font-medium text-stone-900 underline decoration-stone-300 hover:decoration-stone-600"
              >
                {source.title}
              </a>
            </div>
            <p className="ml-7 text-xs text-stone-500">
              <span className={WEAK.has(source.studyType) ? "text-verdict-weak" : ""}>
                {STUDY_TYPE_LABELS[source.studyType]}
              </span>
              {source.journal ? ` · ${source.journal}` : ""}
              {source.year ? ` · ${source.year}` : ""}
            </p>
          </li>
        ))}
      </ol>

      {citations.length > 0 ? (
        <details className="mt-5 text-sm">
          <summary className="cursor-pointer text-stone-500 hover:text-stone-800">
            Which source backs which claim
          </summary>
          <ul className="mt-2 space-y-1.5">
            {citations.map((citation) => (
              <li key={citation.claim} className="text-stone-600">
                <span className="font-medium text-stone-800">{citation.claim}</span>
                {" — "}
                {citation.handles.map((handle, index) => (
                  <span key={handle}>
                    {index > 0 ? ", " : ""}
                    <a href={`#source-${handle}`} className="underline">
                      {handle}
                    </a>
                  </span>
                ))}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </section>
  );
}
