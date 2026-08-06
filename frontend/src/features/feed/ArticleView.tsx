/**
 * The full on-tap article.
 *
 * Two columns, and the split is the argument: the claim and the prose on the
 * left, the *warrant* — strongest evidence available, then every paper — pinned
 * on the right. The sidebar is sticky because the reader's question while
 * reading paragraph three is "what is this resting on?", and making them scroll
 * to the bottom to answer it is how a reader learns not to bother.
 *
 * The disclaimer is rendered from a server-provided constant and is not
 * conditional on anything — it is the one element that must appear on every
 * article regardless of verdict, and it is deliberately not model output.
 */

import { useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";

import { ApiError } from "@/lib/api/client";
import { GradeBar } from "@/features/evidence/GradeBar";
import { VerdictLabel } from "@/features/evidence/VerdictLabel";
import { GRADE_NOTES, VERDICT_GLOSS } from "@/features/evidence/labels";
import { fetchArticle, feedKeys } from "@/lib/api/feed";
import { PublishedDate } from "./ArticleCard";
import { ArticleContent } from "./ArticleContent";
import { SourceList } from "./SourceList";
import { subjectBorderTop, subjectLabel, subjectText } from "@/features/evidence/subject";
import {
  ARTICLE_BACK_LINK,
  ARTICLE_BODY_COLUMN,
  ARTICLE_COLUMNS,
  ARTICLE_DISCLAIMER,
  ARTICLE_ERROR_PAGE,
  ARTICLE_ERROR_TITLE,
  ARTICLE_LEDE,
  ARTICLE_META_STRIP,
  ARTICLE_PAGE,
  ARTICLE_SIDEBAR,
  ARTICLE_SKELETON_COLUMNS,
  ARTICLE_SKELETON_PAGE,
  ARTICLE_TITLE,
  BACK_TO_FEED_LINK,
  META_CELL,
  META_CELL_LABEL,
  META_CELL_VALUE,
  SECTION_LABEL_BLOCK,
  SIDEBAR_BLOCK,
  SIDEBAR_GRADE_BAR,
  SIDEBAR_NOTE,
  SIDEBAR_SOURCES,
  SKELETON_KICKER,
  SKELETON_LEDE,
  SKELETON_PARAGRAPH,
  SKELETON_PROSE,
  SKELETON_SIDEBAR,
  SKELETON_SIDEBAR_BLOCK,
  SKELETON_SOURCE_LIST,
  SKELETON_TITLE,
  VERDICT_GLOSS_TEXT,
  articleKicker,
  articleVerdictBlock,
} from "./styles";

export function ArticleView() {
  const { slug = "" } = useParams();

  // Which source the reader last opened from the prose. Lives here because both
  // the chips and the list need it, and neither owns the other.
  const [activeHandle, setActiveHandle] = useState<string | null>(null);

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
      <main className={ARTICLE_ERROR_PAGE}>
        <h1 className={ARTICLE_ERROR_TITLE}>
          {notFound ? "Article not found" : "Something went wrong"}
        </h1>
        <Link to="/" className={BACK_TO_FEED_LINK}>
          Back to the feed
        </Link>
      </main>
    );
  }

  const subject = article.subject ?? null;
  const kicker = subjectLabel(subject);

  return (
    <main className={ARTICLE_PAGE}>
      <Link to="/" className={ARTICLE_BACK_LINK}>
        ← All articles
      </Link>

      <div className={ARTICLE_COLUMNS}>
        <article className={ARTICLE_BODY_COLUMN}>
          <div className={articleVerdictBlock(subjectBorderTop(subject))}>
            {kicker ? (
              <div className={articleKicker(subjectText(subject))}>{kicker}</div>
            ) : null}

            <VerdictLabel
              verdict={article.verdict}
              qualifier={article.verdictQualifier}
              size="lg"
            />

            <p className={VERDICT_GLOSS_TEXT}>{VERDICT_GLOSS[article.verdict]}</p>
          </div>

          <h1 className={ARTICLE_TITLE}>{article.headline}</h1>
          <p className={ARTICLE_LEDE}>{article.summary}</p>

          <div className={ARTICLE_META_STRIP}>
            <MetaCell label="Product" value={article.product} />
            {article.targetClaims.length > 0 ? (
              <MetaCell
                label="Claims assessed"
                value={article.targetClaims.join(" · ")}
              />
            ) : null}
            {article.ingredients.length > 0 ? (
              <MetaCell
                label="Ingredients"
                value={article.ingredients.join(" · ")}
              />
            ) : null}
            <MetaCell
              label="Published"
              value={<PublishedDate iso={article.publishedAt} />}
            />
          </div>

          <ArticleContent
            doc={article.content}
            sources={article.sources}
            onCite={setActiveHandle}
          />

          <p className={ARTICLE_DISCLAIMER}>{article.disclaimer}</p>
        </article>

        <aside className={ARTICLE_SIDEBAR}>
          <div className={SIDEBAR_BLOCK}>
            <span className={SECTION_LABEL_BLOCK}>Strongest evidence available</span>
            <GradeBar
              grade={article.evidenceGrade}
              showLabel
              className={SIDEBAR_GRADE_BAR}
            />
            <p className={SIDEBAR_NOTE}>{GRADE_NOTES[article.evidenceGrade]}</p>
          </div>

          <div className={SIDEBAR_SOURCES}>
            <SourceList
              sources={article.sources}
              citations={article.citations}
              activeHandle={activeHandle}
            />
          </div>
        </aside>
      </div>
    </main>
  );
}

function MetaCell({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className={META_CELL}>
      <span className={META_CELL_LABEL}>{label}</span>
      <span className={META_CELL_VALUE}>{value}</span>
    </div>
  );
}

/**
 * Skeleton, not a spinner: the article is a two-column layout and a spinner
 * collapses it, so the page reflows the moment content lands.
 */
function ArticleSkeleton() {
  return (
    <main
      className={ARTICLE_SKELETON_PAGE}
      aria-busy="true"
      aria-label="Loading article"
    >
      <div className={ARTICLE_SKELETON_COLUMNS}>
        <div>
          <div className={SKELETON_KICKER} />
          <div className={SKELETON_TITLE} />
          <div className={SKELETON_LEDE} />
          <div className={SKELETON_PROSE}>
            {[0, 1, 2].map((index) => (
              <div key={index} className={SKELETON_PARAGRAPH} />
            ))}
          </div>
        </div>
        <div className={SKELETON_SIDEBAR}>
          <div className={SKELETON_SIDEBAR_BLOCK} />
          <div className={SKELETON_SOURCE_LIST} />
        </div>
      </div>
    </main>
  );
}
