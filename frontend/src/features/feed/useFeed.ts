/**
 * The feed query, split out from the grid that draws it.
 *
 * The filter panel needs to know how many articles are showing and which
 * subjects are present; the grid needs the same items to lay out. Rather than
 * fetch twice or thread a callback back up out of the grid, the route owns the
 * query through this hook and hands the result to both.
 *
 * This is the React binding only — the fetch itself stays in `lib/api/feed.ts`,
 * framework-agnostic, so the Next.js port can call it from a server component
 * without dragging react-query along (DESIGN.md §3.1).
 */

import { useInfiniteQuery } from "@tanstack/react-query";

import { SUBJECTS } from "@/features/evidence/subject";
import { feedKeys, fetchFeed } from "@/lib/api/feed";
import type { FeedCard, Subject, Verdict } from "@/types/api";

export interface Feed {
  items: FeedCard[];
  status: "pending" | "error" | "success";
  error: unknown;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  fetchNextPage: () => void;
  refetch: () => void;
}

export function useFeed(verdict: Verdict | null): Feed {
  const query = useInfiniteQuery({
    // Verdict is a *server* filter: the feed is paginated, so narrowing it
    // client-side would only narrow the pages already loaded.
    queryKey: feedKeys.list(verdict ?? undefined),
    queryFn: ({ pageParam }) =>
      fetchFeed({ cursor: pageParam, verdict: verdict ?? undefined }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  });

  return {
    items: query.data?.pages.flatMap((page) => page.items) ?? [],
    status: query.status,
    error: query.error,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    fetchNextPage: () => void query.fetchNextPage(),
    refetch: () => void query.refetch(),
  };
}

/**
 * Subjects actually present in what has loaded.
 *
 * Deliberately derived rather than hard-coded from the enum: while the API does
 * not serve `subject` this returns empty, and the filter panel hides the whole
 * Subject section instead of offering five chips that match nothing. When the
 * backend gains the field the row appears on its own.
 *
 * Returned in the enum's canonical order, not in the order they happen to
 * appear — otherwise the chips reshuffle as the reader scrolls and more pages
 * load, which moves the one they were reaching for.
 */
export function subjectsPresent(items: FeedCard[]): Subject[] {
  const seen = new Set<Subject>();
  for (const item of items) if (item.subject) seen.add(item.subject);
  return SUBJECTS.filter((subject) => seen.has(subject));
}
