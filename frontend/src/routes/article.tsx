import { ArticleView } from "@/features/feed/ArticleView";

/**
 * Public article page.
 *
 * The route that would gain the most from the Next.js port: this is what would
 * be server-rendered and indexed, with generateMetadata and Article JSON-LD.
 */
export function ArticleRoute() {
  return <ArticleView />;
}
