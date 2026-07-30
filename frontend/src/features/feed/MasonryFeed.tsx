/**
 * The public masonry feed.
 *
 * Virtualised with `masonic`, which only renders cards near the viewport. That
 * matters more here than in a typical grid: this feed is designed to grow
 * indefinitely and cards are variable-height, so an unvirtualised list degrades
 * on exactly the axis the product is meant to scale on.
 *
 * Note for a future Next.js port (DESIGN.md §3.1): `masonic` measures DOM
 * nodes, so it is client-only. It would need `dynamic(..., { ssr: false })`
 * with a static single-column grid as the server-rendered fallback — which is
 * also the better mobile layout, so the fallback is not wasted work.
 */

import { useCallback, useEffect, useRef } from "react";
import { Masonry } from "masonic";
import { useInfiniteQuery } from "@tanstack/react-query";

import { fetchFeed, feedKeys } from "@/lib/api/feed";
import { ArticleCard } from "./ArticleCard";
import type { FeedCard, Verdict } from "@/types/api";

interface Props {
  verdict?: Verdict;
}

export function MasonryFeed({ verdict }: Props) {
  const {
    data,
    error,
    status,
    fetchNextPage,
    hasNextPage,
    isFetchingNextPage,
    refetch,
  } = useInfiniteQuery({
    queryKey: feedKeys.list(verdict),
    queryFn: ({ pageParam }) => fetchFeed({ cursor: pageParam, verdict }),
    initialPageParam: undefined as string | undefined,
    getNextPageParam: (last) => last.nextCursor ?? undefined,
  });

  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasNextPage) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      // Start loading before the sentinel is visible, so the next page is
      // usually in place by the time the reader reaches the bottom.
      { rootMargin: "600px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const renderCard = useCallback(
    ({ data: card }: { data: FeedCard }) => <ArticleCard card={card} />,
    [],
  );

  if (status === "pending") return <FeedSkeleton />;

  if (status === "error") {
    return (
      <ErrorState
        message={error instanceof Error ? error.message : "Could not load the feed"}
        onRetry={() => void refetch()}
      />
    );
  }

  const items = data.pages.flatMap((page) => page.items);
  if (items.length === 0) return <EmptyState verdict={verdict} />;

  return (
    <>
      <Masonry
        items={items}
        columnWidth={280}
        columnGutter={16}
        overscanBy={2}
        // Keyed by slug so masonic reuses cells across page appends rather
        // than remounting the whole grid on every fetch.
        itemKey={(card: FeedCard) => card.slug}
        render={renderCard}
      />
      <div ref={sentinel} aria-hidden className="h-px" />
      {isFetchingNextPage ? (
        <p className="py-6 text-center text-sm text-stone-500">Loading more…</p>
      ) : null}
    </>
  );
}

/**
 * Skeleton cards rather than a spinner: a spinner collapses the layout, so the
 * page jumps when content lands. Varied heights match the real masonry shape.
 */
function FeedSkeleton() {
  const heights = [180, 140, 220, 160, 200, 150, 190, 170];
  return (
    <div
      className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4"
      aria-busy="true"
      aria-label="Loading articles"
    >
      {heights.map((height, index) => (
        <div
          key={index}
          style={{ height }}
          className="animate-pulse rounded-xl border border-stone-200 bg-stone-100"
        />
      ))}
    </div>
  );
}

function EmptyState({ verdict }: { verdict?: Verdict }) {
  return (
    <div className="py-16 text-center">
      <p className="text-stone-600">
        {verdict
          ? "No published articles with that verdict yet."
          : "No published articles yet."}
      </p>
      <p className="mt-1 text-sm text-stone-500">
        Every article here is reviewed by a person before it goes live.
      </p>
    </div>
  );
}

function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="py-16 text-center">
      <p className="text-stone-700">{message}</p>
      <button
        onClick={onRetry}
        className="mt-3 rounded-lg border border-stone-300 px-3 py-1.5 text-sm hover:bg-stone-50"
      >
        Try again
      </button>
    </div>
  );
}
