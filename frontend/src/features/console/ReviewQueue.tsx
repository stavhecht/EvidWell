/**
 * The review queue.
 *
 * Pending review is oldest-first, deliberately: newest-first lets a
 * slow-moving queue strand old drafts behind fresher ones forever.
 *
 * The "Failed validation" tab is not an error log. Those drafts can never be
 * approved, but a run of them is the signal that the synthesis prompt has
 * regressed — hiding them would make the grounding check look like silence
 * rather than like enforcement.
 *
 * Each row carries both signals at once: the verdict mark on the left, the
 * evidence grade bar on the right. A reviewer triaging twenty drafts is really
 * asking "which of these is a confident verdict standing on thin evidence?",
 * and that is a question about the *pair*, answerable here without opening one.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Trash2 } from "lucide-react";

import { consoleKeys, createRun, fetchQueue, rejectArticle } from "@/lib/api/console";
import { GradeBar } from "@/features/evidence/GradeBar";
import { VerdictMark } from "@/features/evidence/VerdictMark";
import { GRADE_LABELS, VERDICT_LABELS } from "@/features/evidence/labels";
import { subjectBorderLeft } from "@/features/evidence/subject";
import type { ArticleStatus, QueueItem } from "@/types/api";
import { useAuth } from "./auth";
import { PRIMARY } from "./controls";
import {
  CONSOLE_PAGE,
  DISCARD_BUTTON,
  NEW_RUN_BLURB_FIELD,
  NEW_RUN_CONFIRMATION,
  NEW_RUN_ERROR,
  NEW_RUN_FORM,
  NEW_RUN_ROW,
  NEW_RUN_TOPIC_FIELD,
  QUEUE_FOOTNOTE,
  QUEUE_HEADER,
  QUEUE_LIST,
  QUEUE_MESSAGE,
  QUEUE_ROW_ASIDE,
  QUEUE_ROW_ERROR,
  QUEUE_ROW_GRADE,
  QUEUE_ROW_GRADE_LABEL,
  QUEUE_ROW_HEADLINE,
  QUEUE_ROW_LINK,
  QUEUE_ROW_META,
  QUEUE_ROW_SIGNALS,
  QUEUE_STANDFIRST,
  QUEUE_TITLE,
  QUEUE_VALIDATION_BADGE,
  QUEUE_VERDICT_WORDING,
  SIGNED_IN_AS,
  SIGN_OUT_ACTION,
  TAB_BAR,
  TAB_COUNT,
  WEAK_EVIDENCE_FLAG,
  queueRow,
  queueTab,
} from "./styles";

const TABS: { status: ArticleStatus; label: string }[] = [
  { status: "pending_review", label: "Pending review" },
  { status: "validation_failed", label: "Failed validation" },
  { status: "published", label: "Published" },
  { status: "rejected", label: "Rejected" },
];

export function ReviewQueue() {
  const [status, setStatus] = useState<ArticleStatus>("pending_review");
  const { reviewer, logout } = useAuth();

  const { data, status: queryStatus } = useQuery({
    queryKey: consoleKeys.queue(status),
    queryFn: () => fetchQueue({ status }),
  });

  return (
    <main className={CONSOLE_PAGE}>
      <div className={QUEUE_HEADER}>
        <div>
          <h1 className={QUEUE_TITLE}>Review queue</h1>
          <p className={QUEUE_STANDFIRST}>
            Oldest first. Nothing publishes without an approval recorded against a
            named reviewer.
          </p>
        </div>
        <span className={SIGNED_IN_AS}>
          Signed in as {reviewer?.displayName ?? "—"} ·
          <button onClick={logout} className={SIGN_OUT_ACTION}>
            Sign out
          </button>
        </span>
      </div>

      <NewRunForm />

      <nav className={TAB_BAR}>
        {TABS.map((tab) => (
          <button
            key={tab.status}
            onClick={() => setStatus(tab.status)}
            aria-current={status === tab.status ? "page" : undefined}
            className={queueTab(status === tab.status)}
          >
            {tab.label}
            {/* Only the open tab's count is known — the queue is fetched one
                status at a time. Showing all four would mean four requests on
                every console load, or a count endpoint that does not exist. */}
            {status === tab.status && data ? (
              <span className={TAB_COUNT}>{data.items.length}</span>
            ) : null}
          </button>
        ))}
      </nav>

      {queryStatus === "pending" ? (
        <p className={QUEUE_MESSAGE}>Loading…</p>
      ) : (data?.items.length ?? 0) === 0 ? (
        <p className={QUEUE_MESSAGE}>
          {status === "pending_review" ? "Nothing waiting for review." : "Nothing here."}
        </p>
      ) : (
        <ul className={QUEUE_LIST}>
          {data?.items.map((item) => (
            <QueueRow key={item.id} item={item} tab={status} />
          ))}
        </ul>
      )}

      <p className={QUEUE_FOOTNOTE}>
        Failed-validation drafts stay visible: a run of them is how a synthesis-prompt
        regression shows itself.
      </p>
    </main>
  );
}

/**
 * One queue row, with its discard action.
 *
 * The action sits *beside* the link rather than inside it — a button nested in
 * an anchor is invalid markup and swallows the click on some browsers.
 *
 * Discard is a rejection, not a delete. There is no DELETE on articles by
 * design: the reason a draft was thrown away is the best available signal for
 * fixing the synthesis prompt. Rejected rows stay readable under the Rejected
 * tab. It is offered on validation_failed too, because otherwise those drafts
 * have no action at all and pile up in a tab nobody can clear.
 */
function QueueRow({ item, tab }: { item: QueueItem; tab: ArticleStatus }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<string | null>(null);

  const discardable = tab === "pending_review" || tab === "validation_failed";

  const discard = useMutation({
    mutationFn: (reason: string) => rejectArticle(item.id, reason),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["console"] }),
    onError: () => setError("Could not discard this draft."),
  });

  return (
    <li className={queueRow(subjectBorderLeft(item.subject ?? null))}>
      <Link to={`/console/review/${item.id}`} className={QUEUE_ROW_LINK}>
        <div className={QUEUE_ROW_SIGNALS}>
          <VerdictMark verdict={item.verdict} size="sm" />
          <span className={QUEUE_VERDICT_WORDING}>{VERDICT_LABELS[item.verdict]}</span>
          <span className={QUEUE_VALIDATION_BADGE}>{item.validationBadge}</span>
          {item.hasWeakEvidence ? (
            <span className={WEAK_EVIDENCE_FLAG}>Rests on weak study types</span>
          ) : null}
        </div>

        <p className={QUEUE_ROW_HEADLINE}>{item.headline}</p>
        <p className={QUEUE_ROW_META}>
          {item.topic} · queued {relativeAge(item.createdAt)}
        </p>
        {error ? <p className={QUEUE_ROW_ERROR}>{error}</p> : null}
      </Link>

      <div className={QUEUE_ROW_ASIDE}>
        <div className={QUEUE_ROW_GRADE}>
          <GradeBar grade={item.evidenceGrade} />
          <span className={QUEUE_ROW_GRADE_LABEL}>
            {GRADE_LABELS[item.evidenceGrade]}
          </span>
        </div>

        {discardable ? (
          <button
            onClick={() => {
              setError(null);
              const reason = window.prompt(
                `Reason for discarding “${item.headline}” (required)`,
              );
              if (reason?.trim()) discard.mutate(reason.trim());
            }}
            disabled={discard.isPending}
            title="Moves this draft to Rejected. The reason is kept."
            aria-label={`Discard ${item.headline}`}
            className={DISCARD_BUTTON}
          >
            <Trash2 size={14} aria-hidden />
          </button>
        ) : null}
      </div>
    </li>
  );
}

/**
 * Queue a topic for generation.
 *
 * Returns 202 — generation takes minutes — so this confirms submission rather
 * than waiting for a draft. The confirmation says plainly that nothing it
 * produces can publish itself.
 */
function NewRunForm() {
  const [topic, setTopic] = useState("");
  const [blurb, setBlurb] = useState("");
  const queryClient = useQueryClient();

  const submit = useMutation({
    mutationFn: () => createRun(topic.trim(), blurb.trim() || undefined),
    onSuccess: () => {
      setTopic("");
      setBlurb("");
      void queryClient.invalidateQueries({ queryKey: consoleKeys.runs });
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (topic.trim()) submit.mutate();
  }

  return (
    <form onSubmit={onSubmit} className={NEW_RUN_FORM}>
      <div className={NEW_RUN_ROW}>
        <input
          value={topic}
          onChange={(event) => setTopic(event.target.value)}
          placeholder="Topic, e.g. ashwagandha for stress"
          aria-label="Topic to generate a draft for"
          className={NEW_RUN_TOPIC_FIELD}
        />
        <button
          type="submit"
          disabled={!topic.trim() || submit.isPending}
          className={PRIMARY}
        >
          {submit.isPending ? "Queueing…" : "Generate draft"}
        </button>
      </div>

      <textarea
        value={blurb}
        onChange={(event) => setBlurb(event.target.value)}
        placeholder="Optional marketing copy — claims are extracted only from what it actually says"
        aria-label="Marketing copy to extract claims from"
        rows={2}
        className={NEW_RUN_BLURB_FIELD}
      />

      {submit.isSuccess ? (
        <p className={NEW_RUN_CONFIRMATION}>
          Queued. The draft will appear here for review once generated — nothing
          publishes without your approval.
        </p>
      ) : null}
      {submit.isError ? (
        <p className={NEW_RUN_ERROR}>Could not queue that topic.</p>
      ) : null}
    </form>
  );
}

/**
 * "3 hours ago" / "yesterday" / "2 days ago".
 *
 * Relative rather than absolute because the queue's job is triage: how long a
 * draft has been waiting is the actionable fact, and a timestamp makes the
 * reviewer do that subtraction themselves on every row.
 */
function relativeAge(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "recently";

  const minutes = Math.max(0, Math.round((Date.now() - then) / 60_000));
  if (minutes < 60) return minutes <= 1 ? "just now" : `${minutes} minutes ago`;

  const hours = Math.round(minutes / 60);
  if (hours < 24) return hours === 1 ? "an hour ago" : `${hours} hours ago`;

  const days = Math.round(hours / 24);
  return days === 1 ? "yesterday" : `${days} days ago`;
}
