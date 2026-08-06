/**
 * The public masonry grid.
 *
 * Virtualised with `masonic`, which only renders cards near the viewport. That
 * matters more here than in a typical grid: this feed is designed to grow
 * indefinitely and cards are variable-height, so an unvirtualised list degrades
 * on exactly the axis the product is meant to scale on.
 *
 * The design comp lays the feed out with plain CSS `column-width`, which is the
 * simpler mechanism and produces the same picture — but CSS columns render
 * every card in the document, so it is the one part of the comp not carried
 * over literally. The measurements are: 320px columns, 18px gutter.
 *
 * Note for a future Next.js port (DESIGN.md §3.1): `masonic` measures DOM
 * nodes, so it is client-only. It would need `dynamic(..., { ssr: false })`
 * with a static single-column grid as the server-rendered fallback — which is
 * also the better mobile layout, so the fallback is not wasted work.
 */

import { useCallback, useEffect, useRef } from "react";
import { Masonry } from "masonic";

import { ArticleCard } from "./ArticleCard";
import {
  CLEAR_FILTERS_ACTION,
  FEED_STATE,
  FEED_STATE_BODY,
  FEED_STATE_TITLE,
  LOADING_MORE,
  RETRY_BUTTON,
  SCROLL_SENTINEL,
  SKELETON_CARD,
  SKELETON_GRID,
} from "./styles";
import type { Feed } from "./useFeed";
import type { FeedCard } from "@/types/api";

interface Props extends Feed {
  /** True when the reader has narrowed the feed, so "empty" needs a way out. */
  filtered: boolean;
  onClearFilters: () => void;
}

export function MasonryFeed({
  items,
  status,
  error,
  hasNextPage,
  isFetchingNextPage,
  fetchNextPage,
  refetch,
  filtered,
  onClearFilters,
}: Props) {
  const sentinel = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = sentinel.current;
    if (!node || !hasNextPage) return;

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting && !isFetchingNextPage) fetchNextPage();
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
        onRetry={refetch}
      />
    );
  }

  if (items.length === 0) {
    return <EmptyState filtered={filtered} onClearFilters={onClearFilters} />;
  }

  return (
    <>
      <Masonry
        items={items}
        columnWidth={320}
        columnGutter={18}
        overscanBy={2}
        // Keyed by slug so masonic reuses cells across page appends rather
        // than remounting the whole grid on every fetch.
        itemKey={(card: FeedCard) => card.slug}
        render={renderCard}
      />
      <div ref={sentinel} aria-hidden className={SCROLL_SENTINEL} />
      {isFetchingNextPage ? <p className={LOADING_MORE}>Loading more…</p> : null}
    </>
  );
}

/**
 * Skeleton cards rather than a spinner: a spinner collapses the layout, so the
 * page jumps when content lands. Varied heights match the real masonry shape,
 * and the 3px top rule keeps the grid's structure visible while it fills.
 */
function FeedSkeleton() {
  const heights = [180, 140, 220, 160, 200, 150, 190, 170];
  return (
    <div className={SKELETON_GRID} aria-busy="true" aria-label="Loading articles">
      {heights.map((height, index) => (
        <div key={index} style={{ height }} className={SKELETON_CARD} />
      ))}
    </div>
  );
}

function EmptyState({
  filtered,
  onClearFilters,
}: {
  filtered: boolean;
  onClearFilters: () => void;
}) {
  return (
    <div className={FEED_STATE}>
      <p className={FEED_STATE_TITLE}>
        {filtered
          ? "Nothing published with that filter yet."
          : "Nothing published yet."}
      </p>
      <p className={FEED_STATE_BODY}>
        Every article here is reviewed by a person before it goes live.
      </p>
      {filtered ? (
        <button onClick={onClearFilters} className={CLEAR_FILTERS_ACTION}>
          Clear filters
        </button>
      ) : null}
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className={FEED_STATE}>
      <p className={FEED_STATE_TITLE}>{message}</p>
      <button onClick={onRetry} className={RETRY_BUTTON}>
        Try again
      </button>
    </div>
  );
}
