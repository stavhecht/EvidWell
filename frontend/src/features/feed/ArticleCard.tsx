/**
 * One card in the masonry feed.
 *
 * Every field here is derived server-side from the approved article
 * (services/card.py) — there is no card-specific model output. That is what
 * guarantees a card cannot promise something the article walks back.
 *
 * Reading order is the argument order: subject, then verdict, then the scope
 * the verdict applies to, then the headline. A reader who stops after two lines
 * has still got the honest version — which is why the qualifier sits *above*
 * the headline rather than trailing it as a footnote.
 */

import { Link } from "react-router-dom";

import { VerdictMark } from "@/features/evidence/VerdictMark";
import { VERDICT_LABELS } from "@/features/evidence/labels";
import { cardVerdictWording } from "@/features/evidence/styles";
import { subjectBorderTop, subjectLabel, subjectText } from "@/features/evidence/subject";
import {
  CARD_EXCERPT,
  CARD_FOOTER,
  CARD_HEADLINE,
  CARD_QUALIFIER,
  CARD_VERDICT_ROW,
  cardKicker,
  feedCard,
} from "./styles";
import type { FeedCard } from "@/types/api";

export function ArticleCard({ card }: { card: FeedCard }) {
  const subject = card.subject ?? null;
  const label = subjectLabel(subject);

  return (
    <Link to={`/a/${card.slug}`} className={feedCard(subjectBorderTop(subject))}>
      {label ? <div className={cardKicker(subjectText(subject))}>{label}</div> : null}

      <div className={CARD_VERDICT_ROW}>
        <VerdictMark verdict={card.verdict} />
        <span className={cardVerdictWording(card.verdict)}>
          {VERDICT_LABELS[card.verdict]}
        </span>
      </div>

      {card.verdictQualifier ? (
        <div className={CARD_QUALIFIER}>{card.verdictQualifier}</div>
      ) : null}

      <h2 className={CARD_HEADLINE}>{card.headline}</h2>

      <p className={CARD_EXCERPT}>{card.excerpt}</p>

      <div className={CARD_FOOTER}>
        <PublishedDate iso={card.publishedAt} />
      </div>
    </Link>
  );
}

/**
 * `<time>` rather than a plain string, so the machine-readable date survives
 * formatting — this is the element a future Article JSON-LD block reads from.
 */
export function PublishedDate({ iso }: { iso: string }) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return <>{iso}</>;

  return (
    <time dateTime={iso}>
      {date.toLocaleDateString("en-GB", {
        day: "numeric",
        month: "short",
        year: "numeric",
      })}
    </time>
  );
}
