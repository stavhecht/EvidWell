/**
 * The feed query, split out from the grid that draws it.
 *
 * The feed heading needs to know how many articles are showing; the grid needs
 * the same items to lay out. Rather than fetch twice or thread a callback back
 * up out of the grid, the route owns the query through this hook and hands the
 * result to both.
 *
 * This is the React binding only — the fetch itself stays in `lib/api/feed.ts`,
 * framework-agnostic, so the Next.js port can call it from a server component
 * without dragging react-query along (DESIGN.md §3.1).
 *
 * **Which narrowing happens where, and why.** Verdict and subject are *server*
 * filters: the feed is paginated, so narrowing those client-side would only
 * narrow the pages already loaded and a category with nothing on page one would
 * look empty. Free text is the opposite — there is no search endpoint, and
 * inventing a `?q=` that the backend answers with a `LIKE` over card columns
 * would be a search that quietly misses the body of every article. Matching
 * what has loaded is a smaller promise, and `matchedWithin` below is what lets
 * the UI make exactly that promise rather than implying a full-corpus search.
 */

import { useMemo } from "react";
import { useInfiniteQuery } from "@tanstack/react-query";

import { feedKeys, fetchFeed, type FeedFilter } from "@/lib/api/feed";
import type { FeedCard } from "@/types/api";

export interface Feed {
  items: FeedCard[];
  status: "pending" | "error" | "success";
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  refetch: () => void;
  /**
   * How many cards the text search was applied to. Zero when nothing is being
   * searched. The empty state uses it to say "nothing in the N loaded so far"
   * rather than "nothing published", which would be a claim about the archive.
   */
  matchedWithin: number;
}

export function useFeed(filter: FeedFilter & { query?: string }): Feed {
  const serverFilter: FeedFilter = {
    verdict: filter.verdict ?? null,
    subject: filter.subject ?? null,
    personalise: filter.personalise,
  };

  const query = useInfiniteQuery({
    queryKey: feedKeys.list(serverFilter),
    queryFn: ({ pageParam }) => fetchFeed({ cursor: pageParam, ...serverFilter }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  });

  const loaded = useMemo(
    () => query.data?.pages.flatMap((page) => page.items) ?? [],
    [query.data],
  );

  const text = (filter.query ?? "").trim().toLowerCase();
  const items = useMemo(
    () => (text ? loaded.filter((card) => matches(card, text)) : loaded),
    [loaded, text],
  );

  return {
    items,
    status: query.status,
    error: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: () => void query.fetchNextPage(),
    refetch: () => void query.refetch(),
    matchedWithin: text ? loaded.length : 0,
  };
}

/**
 * Headline, excerpt and the verdict's scope limit.
 *
 * The qualifier is in here on purpose — it is where a card says "for
 * pigmentation" or "at 10–20%", which is exactly the kind of thing someone
 * types into a search box, and it is not repeated anywhere else on the tile.
 */
function matches(card: FeedCard, text: string): boolean {
  return [card.headline, card.excerpt, card.verdictQualifier ?? ""]
    .join(" ")
    .toLowerCase()
    .includes(text);
}
