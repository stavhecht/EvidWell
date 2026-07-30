/**
 * The full on-tap article.
 *
 * The disclaimer is rendered from a server-provided constant and is not
 * conditional on anything — it is the one element that must appear on every
 * article regardless of verdict, and it is deliberately not model output.
 */

import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import { fetchArticle, feedKeys } from "@/lib/api/feed";
import { ArticleContent } from "./ArticleContent";
import { SourceList } from "./SourceList";
import { VerdictBadge } from "./VerdictBadge";

export function ArticleView() {
  const { slug = "" } = useParams();
  const {
    data: article,
    status,
    error,
  } = useQuery({
    queryKey: feedKeys.article(slug),
    queryFn: () => fetchArticle(slug),
    enabled: Boolean(slug),
    // A 404 here means "not published", which will not become true by
    // retrying — and retrying an unpublished slug is how a draft-preview
    // channel accidentally gets built.
    retry: (count, err) =>
      !(err instanceof ApiError && err.status === 404) && count < 2,
  });

  if (status === "pending") return <ArticleSkeleton />;

  if (status === "error") {
    const notFound = error instanceof ApiError && error.status === 404;
    return (
      <main className="mx-auto max-w-2xl px-4 py-16 text-center">
        <h1 className="text-xl font-semibold text-stone-900">
          {notFound ? "Article not found" : "Something went wrong"}
        </h1>
        <Link to="/" className="mt-4 inline-block text-sm underline">
          Back to the feed
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-10">
      <Link
        to="/"
        className="inline-flex items-center gap-1 text-sm text-stone-500 hover:text-stone-900"
      >
        <ArrowLeft size={14} aria-hidden /> All articles
      </Link>

      <header className="mt-6">
        <VerdictBadge
          verdict={article.verdict}
          qualifier={article.verdictQualifier}
        />
        <h1 className="mt-3 text-2xl font-bold leading-tight text-stone-900">
          {article.headline}
        </h1>
        <p className="mt-2 text-lg text-stone-700">{article.summary}</p>

        {article.ingredients.length > 0 ? (
          <p className="mt-3 text-sm text-stone-500">
            Ingredients assessed: {article.ingredients.join(", ")}
          </p>
        ) : null}
      </header>

      <ArticleContent doc={article.content} />

      <SourceList sources={article.sources} citations={article.citations} />

      <p className="mt-10 border-t border-stone-200 pt-4 text-sm text-stone-500">
        {article.disclaimer}
      </p>
    </main>
  );
}

function ArticleSkeleton() {
  return (
    <main className="mx-auto max-w-2xl animate-pulse px-4 py-10" aria-busy="true">
      <div className="h-6 w-40 rounded-full bg-stone-100" />
      <div className="mt-4 h-8 w-4/5 rounded bg-stone-100" />
      <div className="mt-3 h-5 w-full rounded bg-stone-100" />
      <div className="mt-8 space-y-3">
        {[0, 1, 2].map((index) => (
          <div key={index} className="h-16 rounded bg-stone-100" />
        ))}
      </div>
    </main>
  );
}
