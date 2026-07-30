import { useState } from "react";

import { MasonryFeed } from "@/features/feed/MasonryFeed";
import type { Verdict } from "@/types/api";

const FILTERS: { value: Verdict | undefined; label: string }[] = [
  { value: undefined, label: "All" },
  { value: "supported", label: "Supported" },
  { value: "mixed", label: "Mixed" },
  { value: "weak", label: "Weak" },
  { value: "no_evidence", label: "No evidence" },
];

/** The public feed. */
export function FeedRoute() {
  const [verdict, setVerdict] = useState<Verdict | undefined>(undefined);

  return (
    <main className="mx-auto max-w-6xl px-4 py-8">
      <header>
        <h1 className="text-xl font-bold text-stone-900">
          Wellness claims, checked against the evidence
        </h1>
        <p className="mt-1 text-sm text-stone-600">
          Every article cites real studies and is reviewed by a person before it
          is published.
        </p>
      </header>

      <nav className="mt-5 flex flex-wrap gap-2" aria-label="Filter by verdict">
        {FILTERS.map((filter) => (
          <button
            key={filter.label}
            onClick={() => setVerdict(filter.value)}
            aria-pressed={verdict === filter.value}
            className={`rounded-full px-3 py-1 text-sm ring-1 ring-inset transition ${
              verdict === filter.value
                ? "bg-stone-900 text-white ring-stone-900"
                : "bg-white text-stone-600 ring-stone-300 hover:bg-stone-50"
            }`}
          >
            {filter.label}
          </button>
        ))}
      </nav>

      <div className="mt-6">
        <MasonryFeed verdict={verdict} />
      </div>

      <footer className="mt-12 border-t border-stone-200 pt-4 text-xs text-stone-500">
        Informational only — not medical advice.
      </footer>
    </main>
  );
}
