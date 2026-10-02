/**
 * Trending topics from the research agent — the desk half of `app/research`.
 *
 * "Find trending topics" starts the same run n8n's weekly schedule starts: it
 * reads Google Trends, web search and news for what people are asking about,
 * checks each topic against PubMed and Europe PMC, and proposes the strongest
 * few. It proposes; nothing here is generated until a reviewer presses
 * Generate draft, and nothing generated publishes without an approval.
 *
 * Three things this panel is careful about, for the same reasons as
 * `TrendCandidates`:
 *
 * **Popularity and evidence are shown apart.** Each proposal says how the
 * topic is trending *and*, separately, what the literature holds — "search
 * interest +100%" and "2 meta-analyses, 5 trials" are different claims, and a
 * single number would hide which one a topic rests on.
 *
 * **Missing data is named, not zeroed.** When news or web search could not be
 * reached the score says "not measured: news", matching the server, which
 * leaves the component out rather than guessing it.
 *
 * **What was turned away is visible.** A topic dropped for thin evidence is
 * listed with its reason, so an empty proposal list reads as a judgement rather
 * than a broken panel.
 */

import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, LoaderCircle, Search } from "lucide-react";

import {
  consoleKeys,
  dismissResearchCandidate,
  fetchResearchRun,
  fetchResearchRuns,
  promoteResearchCandidate,
  startResearchRun,
} from "@/lib/api/console";
import { ApiError } from "@/lib/api/client";
import { SUBJECTS, SUBJECT_LABELS } from "@/features/evidence/subject";
import type {
  ResearchCandidate,
  ResearchCategory,
  ResearchRun,
  ResearchRunRequest,
} from "@/types/api";
import { PRIMARY, SECONDARY } from "./controls";
import {
  CANDIDATE_ACTIONS,
  CANDIDATE_DISMISS_FIELD,
  CANDIDATE_DISMISS_ROW,
  CANDIDATE_EVIDENCE,
  CANDIDATE_MAIN,
  CANDIDATE_MIX,
  CANDIDATE_ROW,
  CANDIDATE_TOPIC,
  CANDIDATES_BADGE,
  CANDIDATES_EMPTY,
  CANDIDATES_ERROR,
  CANDIDATES_HEADER,
  CANDIDATES_LIST,
  CANDIDATES_PANEL,
  CANDIDATES_SCAN,
  CANDIDATES_SCAN_NOTE,
  CANDIDATES_SUPPRESSED,
  CANDIDATES_SUPPRESSED_ROW,
  CANDIDATES_SUPPRESSED_TOPIC,
  CANDIDATES_SUPPRESSED_WHY,
  CANDIDATES_TITLE,
  CANDIDATES_TOGGLE,
  RESEARCH_CATEGORY,
  RESEARCH_DONE,
  RESEARCH_OPTION,
  RESEARCH_OPTIONS,
  RESEARCH_QUESTION,
  RESEARCH_SCORE,
  RESEARCH_SELECT,
  RESEARCH_SUBHEAD,
  candidatesChevron,
} from "./styles";

// The article categories, so the filter here and the feed's drawer cannot drift.
const CATEGORIES: { value: ResearchCategory; label: string }[] = SUBJECTS.map(
  (value) => ({ value, label: SUBJECT_LABELS[value] }),
);

const STAGE_WORDS: Record<string, string> = {
  queued: "waiting for the worker",
  loading_config: "starting",
  discovering_trends: "reading Google Trends",
  expanding_topics: "following related searches",
  building_candidates: "grouping searches into topics",
  checking_novelty: "checking against recent articles",
  researching_news: "counting news coverage",
  researching_science: "counting papers in PubMed and Europe PMC",
  scoring_topics: "scoring",
  searching_web: "searching the web",
  shortlisting: "shortlisting",
  deep_research: "running the article pipeline's own literature query",
  selecting_topics: "choosing",
  backfilling: "looking further down the list",
};

const COMPONENT_WORDS: Record<string, string> = {
  trend_growth: "trend",
  news_momentum: "news",
  scientific_evidence: "evidence",
  source_quality: "sources",
  reader_interest: "interest",
  novelty: "novelty",
};

const POLL_MS = 10_000;

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

/** What the literature holds. Deep counts when present: the article's own query. */
function evidenceLine(candidate: ResearchCandidate): string {
  const science = candidate.signals.science;
  const counts = science.deep?.counts ?? science.primary;
  if (!counts) return "Evidence could not be checked.";
  const status = candidate.evidenceStatus ?? "unknown";
  const source = counts.source === "europe_pmc" ? "Europe PMC" : "PubMed";
  return (
    `Evidence ${status}: ${plural(counts.total, "paper")}, ` +
    `${counts.reviews_or_meta} review${counts.reviews_or_meta === 1 ? "" : "s"}/meta-analyses, ` +
    `${plural(counts.rcts, "trial")} (${source})`
  );
}

/** How it is trending — search, news and the web, in that order. */
function trendLine(candidate: ResearchCandidate): string {
  const parts: string[] = [];
  const { trend, news, web } = candidate.signals;
  if (trend?.is_breakout) parts.push("breakout search");
  else if (trend?.growth_percent != null)
    parts.push(`search interest ${trend.growth_percent >= 0 ? "+" : ""}${Math.round(trend.growth_percent)}%`);
  else if (trend?.rising_percent != null) parts.push(`rising searches +${trend.rising_percent}%`);
  if (news) {
    const before = news.previous == null ? "" : ` (${news.previous} the week before)`;
    parts.push(`${plural(news.recent, "news article")} this week${before}`);
  }
  if (web) parts.push(`${web.authoritative_results} of ${web.relevant_results} web results authoritative`);
  return parts.join(" · ");
}

function scoreLine(candidate: ResearchCandidate): string {
  const { components, unavailable, overall } = candidate.scores;
  if (overall == null) return "";
  const parts = Object.entries(components).map(
    ([name, value]) => `${COMPONENT_WORDS[name] ?? name} ${Math.round(value)}`,
  );
  const missing = unavailable.length
    ? ` · not measured: ${unavailable.map((name) => COMPONENT_WORDS[name] ?? name).join(", ")}`
    : "";
  return `Score ${Math.round(overall)} — ${parts.join(", ")}${missing}`;
}

function describeError(failure: unknown, fallback: string): string {
  if (failure instanceof ApiError) {
    const detail = (failure.detail as { detail?: string } | undefined)?.detail;
    if (detail) return detail;
  }
  return fallback;
}

function ProposalRow({ candidate, runId }: { candidate: ResearchCandidate; runId: string }) {
  const queryClient = useQueryClient();
  const [dismissing, setDismissing] = useState(false);
  const [reason, setReason] = useState("");

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: consoleKeys.researchRun(runId) });
    void queryClient.invalidateQueries({ queryKey: consoleKeys.runs });
  };
  const promote = useMutation({
    mutationFn: () => promoteResearchCandidate(candidate.id),
    onSuccess: invalidate,
  });
  const dismiss = useMutation({
    mutationFn: () => dismissResearchCandidate(candidate.id, reason.trim()),
    onSuccess: invalidate,
  });
  const busy = promote.isPending || dismiss.isPending;
  const decided = candidate.status === "promoted" || candidate.status === "dismissed";

  const onDismiss = (event: FormEvent) => {
    event.preventDefault();
    if (reason.trim()) dismiss.mutate();
  };

  return (
    <div className={CANDIDATE_ROW}>
      <div className={CANDIDATE_MAIN}>
        <p className={CANDIDATE_TOPIC}>{candidate.canonicalTopic}</p>
        {candidate.signals.reader_question ? (
          <p className={RESEARCH_QUESTION}>{candidate.signals.reader_question}</p>
        ) : null}
        <p className={CANDIDATE_EVIDENCE}>{evidenceLine(candidate)}</p>
        {trendLine(candidate) ? <p className={CANDIDATE_MIX}>{trendLine(candidate)}</p> : null}
        {scoreLine(candidate) ? <p className={RESEARCH_SCORE}>{scoreLine(candidate)}</p> : null}
        {(promote.isError || dismiss.isError) && (
          <p className={CANDIDATES_ERROR}>
            {describeError(promote.error ?? dismiss.error, "Could not save that.")}
          </p>
        )}
      </div>

      <div className={CANDIDATE_ACTIONS}>
        {decided ? (
          <span className={RESEARCH_DONE}>
            {candidate.status === "promoted" ? "Draft queued" : "Dismissed"}
          </span>
        ) : (
          <>
            <button
              type="button"
              className={PRIMARY}
              disabled={busy}
              onClick={() => promote.mutate()}
              title="Queues a full generation run. Nothing it produces publishes without your approval."
            >
              {promote.isPending ? (
                <LoaderCircle className="size-3.5 animate-spin" />
              ) : (
                "Generate draft"
              )}
            </button>
            <button
              type="button"
              className={SECONDARY}
              disabled={busy}
              onClick={() => setDismissing((open) => !open)}
            >
              Dismiss
            </button>
          </>
        )}
      </div>

      {dismissing && !decided ? (
        <form className={CANDIDATE_DISMISS_ROW} onSubmit={onDismiss}>
          <input
            className={CANDIDATE_DISMISS_FIELD}
            value={reason}
            onChange={(event) => setReason(event.target.value)}
            placeholder="Why not? A close match will not be proposed again for months."
            aria-label={`Reason for dismissing ${candidate.canonicalTopic}`}
          />
          <button type="submit" className={SECONDARY} disabled={!reason.trim() || busy}>
            Save
          </button>
        </form>
      ) : null}
    </div>
  );
}

/**
 * The latest run, polled while it is queued or running.
 *
 * The list query answers "which run is latest"; the run query carries the
 * candidates. Only the second polls, and only while the run is live, so an
 * idle desk makes two requests on load and then none.
 */
function useLatestRun(): { run: ResearchRun | undefined; error: boolean } {
  const queryClient = useQueryClient();
  const runs = useQuery({
    queryKey: consoleKeys.researchRuns,
    queryFn: () => fetchResearchRuns(1),
  });
  const latestId = runs.data?.[0]?.id;
  const run = useQuery({
    queryKey: consoleKeys.researchRun(latestId ?? "none"),
    queryFn: () => fetchResearchRun(latestId as string),
    enabled: Boolean(latestId),
    staleTime: 0,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === "queued" || status === "running" ? POLL_MS : false;
    },
  });

  // A finished run can change what the queue shows only through a promotion,
  // which invalidates on its own; but the runs list must learn the new status.
  const live = useRef(false);
  useEffect(() => {
    const now = run.data?.status === "queued" || run.data?.status === "running";
    if (live.current && !now) {
      void queryClient.invalidateQueries({ queryKey: consoleKeys.researchRuns });
    }
    live.current = now;
  }, [run.data, queryClient]);

  return { run: run.data, error: runs.isError || run.isError };
}

function statusLine(run: ResearchRun): string {
  if (run.status === "queued" || run.status === "running") {
    const stage = STAGE_WORDS[run.stage ?? "queued"] ?? run.stage ?? "working";
    return `Finding trending topics — ${stage}. This takes 10 to 20 minutes; you can leave this page.`;
  }
  if (run.status === "failed") {
    return `The last run failed: ${run.error?.message ?? "no reason given"}.`;
  }
  const proposed = run.candidates.filter((c) =>
    ["selected", "promoted", "dismissed"].includes(c.status),
  ).length;
  const considered = run.candidates.length;
  const when = run.finishedAt ? new Date(run.finishedAt).toLocaleString() : "";
  const head = `${plural(proposed, "topic")} proposed from ${considered} considered (${run.mode}, ${when}).`;
  return run.notes.length ? `${head} ${run.notes.join(" ")}` : head;
}

/** Providers that did not fully answer, so the reader knows what is missing. */
function providerLine(run: ResearchRun): string | null {
  const problems = Object.entries(run.providerStatus)
    .filter(([, status]) => status.status !== "ok")
    .map(([name, status]) => `${name.replace("_", " ")} ${status.status}`);
  return problems.length ? `Sources not fully available: ${problems.join(", ")}.` : null;
}

export function ResearchPanel() {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState(5);
  const [windowDays, setWindowDays] = useState(7);
  const [categories, setCategories] = useState<ResearchCategory[]>(
    CATEGORIES.map((c) => c.value),
  );

  const queryClient = useQueryClient();
  const { run, error } = useLatestRun();

  const start = useMutation({
    mutationFn: (request: ResearchRunRequest) => startResearchRun(request),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: consoleKeys.researchRuns }),
  });

  const live = run?.status === "queued" || run?.status === "running";
  const busy = live || start.isPending;
  const proposals =
    run?.candidates.filter((c) => ["selected", "promoted", "dismissed"].includes(c.status)) ?? [];
  const undecided = proposals.filter((c) => c.status === "selected").length;
  const others =
    run?.candidates.filter((c) => c.status === "shortlisted" || c.status === "discarded") ?? [];

  const toggleCategory = (value: ResearchCategory) =>
    setCategories((current) =>
      current.includes(value) ? current.filter((c) => c !== value) : [...current, value],
    );

  const onStart = () =>
    start.mutate({
      targetArticleCount: count,
      trendWindowDays: windowDays,
      // Every category is the server's default; send the list only when narrowed.
      categories: categories.length === CATEGORIES.length ? undefined : categories,
    });

  const note = start.isError
    ? describeError(start.error, "Could not start a research run.")
    : run
      ? statusLine(run)
      : null;
  const providers = run && !live ? providerLine(run) : null;

  return (
    <section className={CANDIDATES_PANEL}>
      <div className={CANDIDATES_HEADER}>
        <button
          type="button"
          className={CANDIDATES_TOGGLE}
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronRight className={candidatesChevron(open)} aria-hidden />
          <span className={CANDIDATES_TITLE}>Trending topics</span>
        </button>

        {undecided > 0 ? (
          <span className={CANDIDATES_BADGE} aria-label={`${undecided} proposed`}>
            {undecided}
          </span>
        ) : null}

        <button
          type="button"
          className={CANDIDATES_SCAN}
          disabled={busy || categories.length === 0}
          onClick={onStart}
          title="Reads Google Trends, web search and news, checks each topic in PubMed and Europe PMC, and proposes the strongest. No drafts are generated."
        >
          {busy ? (
            <LoaderCircle className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Search className="size-3.5" aria-hidden />
          )}
          {busy ? "Researching" : "Find trending topics"}
        </button>
      </div>

      {note ? (
        <p className={CANDIDATES_SCAN_NOTE} role="status">
          {note}
        </p>
      ) : null}
      {providers ? <p className={CANDIDATES_SCAN_NOTE}>{providers}</p> : null}

      {open ? (
        <>
          <div className={RESEARCH_OPTIONS}>
            <label className={RESEARCH_OPTION}>
              Topics
              <select
                className={RESEARCH_SELECT}
                value={count}
                onChange={(event) => setCount(Number(event.target.value))}
              >
                {[4, 5, 6].map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
            </label>
            <label className={RESEARCH_OPTION}>
              Trend window
              <select
                className={RESEARCH_SELECT}
                value={windowDays}
                onChange={(event) => setWindowDays(Number(event.target.value))}
              >
                <option value={7}>7 days</option>
                <option value={14}>14 days</option>
              </select>
            </label>
            {CATEGORIES.map((category) => (
              <label key={category.value} className={RESEARCH_CATEGORY}>
                <input
                  type="checkbox"
                  checked={categories.includes(category.value)}
                  onChange={() => toggleCategory(category.value)}
                />
                {category.label}
              </label>
            ))}
          </div>

          <div className={CANDIDATES_LIST}>
            {error ? (
              <p className={CANDIDATES_ERROR}>Could not load research runs.</p>
            ) : !run ? (
              <p className={CANDIDATES_EMPTY}>
                No research run yet. n8n starts one weekly, or press Find trending topics.
              </p>
            ) : proposals.length === 0 && !live ? (
              <p className={CANDIDATES_EMPTY}>
                Nothing proposed: no topic passed the evidence and material checks. The
                reasons are listed below.
              </p>
            ) : (
              proposals.map((candidate) => (
                <ProposalRow key={candidate.id} candidate={candidate} runId={run.id} />
              ))
            )}
          </div>

          {others.length ? (
            <>
              <p className={RESEARCH_SUBHEAD}>Also considered</p>
              <ul className={CANDIDATES_SUPPRESSED}>
                {others.map((candidate) => (
                  <li key={candidate.id} className={CANDIDATES_SUPPRESSED_ROW}>
                    <span className={CANDIDATES_SUPPRESSED_TOPIC}>
                      {candidate.canonicalTopic}
                    </span>
                    <span className={CANDIDATES_SUPPRESSED_WHY}>
                      {candidate.status === "shortlisted"
                        ? `shortlisted, outscored (${Math.round(candidate.overall ?? 0)})`
                        : candidate.discardReason}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
