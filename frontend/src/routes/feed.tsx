/**
 * The public feed.
 *
 * The masthead is the comp's `masthead` voice: a display-size statement of what
 * the product is, set flush left over a 16ch measure, with the promise
 * underneath. The alternatives explored were a compact verbatim header and a
 * two-column split — both read like a blog. This one reads like a masthead,
 * which is the claim the product is making.
 */

import { useMemo, useState } from "react";

import { FeedFilters } from "@/features/feed/FeedFilters";
import { MasonryFeed } from "@/features/feed/MasonryFeed";
import {
  FEED_FOOTER,
  FEED_PAGE,
  MASTHEAD,
  MASTHEAD_STANDFIRST,
  MASTHEAD_TITLE,
} from "@/features/feed/styles";
import { subjectsPresent, useFeed } from "@/features/feed/useFeed";
import type { Subject, Verdict } from "@/types/api";

export function FeedRoute() {
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [subject, setSubject] = useState<Subject | null>(null);
  const [filtersOpen, setFiltersOpen] = useState(false);

  const feed = useFeed(verdict);

  const available = useMemo(() => subjectsPresent(feed.items), [feed.items]);

  /*
   * Subject is narrowed here rather than in the query, because the API has no
   * `subject` parameter to send — it does not serve the field at all yet. The
   * consequence to fix when it does: this only narrows the pages already
   * loaded, so a subject with no match in page one looks empty until the reader
   * scrolls. Harmless while `available` is empty and the control is hidden.
   */
  const items = useMemo(
    () => (subject ? feed.items.filter((item) => item.subject === subject) : feed.items),
    [feed.items, subject],
  );

  const filtered = verdict !== null || subject !== null;

  function clearFilters() {
    setVerdict(null);
    setSubject(null);
  }

  return (
    <main className={FEED_PAGE}>
      <div className={MASTHEAD}>
        <h1 className={MASTHEAD_TITLE}>
          Wellness claims, checked against the evidence
        </h1>
        <p className={MASTHEAD_STANDFIRST}>
          Every article cites real studies and is reviewed by a person before it is
          published. Nothing here is sponsored, and no product is sold.
        </p>
      </div>

      <FeedFilters
        verdict={verdict}
        onVerdict={setVerdict}
        subject={subject}
        onSubject={setSubject}
        availableSubjects={available}
        open={filtersOpen}
        onOpenChange={setFiltersOpen}
        count={
          feed.status === "success"
            ? `${items.length} ${items.length === 1 ? "article" : "articles"} · every one editor-approved`
            : ""
        }
      />

      <MasonryFeed
        {...feed}
        items={items}
        filtered={filtered}
        onClearFilters={clearFilters}
      />

      <footer className={FEED_FOOTER}>
        <span>Informational only — not medical advice.</span>
        <span>Sources: PubMed · Europe PMC · Semantic Scholar · OpenAlex</span>
      </footer>
    </main>
  );
}
