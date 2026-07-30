/**
 * Public feed data access. No auth, no mutations.
 */

import { apiFetch, qs } from "./client";
import type { Article, FeedPage, Verdict } from "@/types/api";

export const feedKeys = {
  all: ["feed"] as const,
  list: (verdict?: Verdict) => ["feed", "list", verdict ?? "all"] as const,
  article: (slug: string) => ["feed", "article", slug] as const,
};

/**
 * One page of published cards.
 *
 * Cursor-paginated rather than offset-paginated: the feed grows at the head,
 * and offsets skip or duplicate items when something publishes mid-scroll.
 * Pairs with useInfiniteQuery.
 *
 * STUB.
 */
export async function fetchFeed(params: {
  cursor?: string;
  limit?: number;
  verdict?: Verdict;
}): Promise<FeedPage> {
  return apiFetch<FeedPage>(`/feed${qs(params)}`);
}

/** Full article with citations and sources. STUB. */
export async function fetchArticle(slug: string): Promise<Article> {
  return apiFetch<Article>(`/articles/${encodeURIComponent(slug)}`);
}
