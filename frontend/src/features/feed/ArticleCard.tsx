/**
 * One card in the masonry feed.
 *
 * Every field here is derived server-side from the approved article
 * (services/card.py) — there is no card-specific model output. That is what
 * guarantees a card cannot promise something the article walks back.
 */

import { Link } from "react-router-dom";

import { VerdictBadge } from "./VerdictBadge";
import type { FeedCard } from "@/types/api";

export function ArticleCard({ card }: { card: FeedCard }) {
  return (
    <Link
      to={`/a/${card.slug}`}
      className="block rounded-xl border border-stone-200 bg-white p-4 transition hover:border-stone-300 hover:shadow-sm"
    >
      {/* Badge above the headline: the verdict is the reason to tap, so it
          should be readable before the headline is finished being read. */}
      <VerdictBadge verdict={card.verdict} qualifier={card.verdictQualifier} size="sm" />
      <h2 className="mt-2 text-base font-semibold leading-snug text-stone-900">
        {card.headline}
      </h2>
      <p className="mt-1.5 text-sm leading-relaxed text-stone-600">{card.excerpt}</p>
    </Link>
  );
}
