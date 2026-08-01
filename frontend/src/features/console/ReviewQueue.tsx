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
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, Trash2 } from "lucide-react";

import { consoleKeys, createRun, fetchQueue, rejectArticle } from "@/lib/api/console";
import { VerdictBadge } from "@/features/feed/VerdictBadge";
import type { ArticleStatus, QueueItem } from "@/types/api";
import { useAuth } from "./auth";

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
    <div className="mx-auto max-w-4xl px-4 py-8">
      <header className="flex items-baseline justify-between">
        <h1 className="text-lg font-semibold text-stone-900">Review queue</h1>
        <div className="flex items-center gap-3 text-sm text-stone-500">
          <span>{reviewer?.displayName}</span>
          <button onClick={logout} className="underline hover:text-stone-900">
            Sign out
          </button>
        </div>
      </header>

      <NewRunForm />

      <nav className="mt-6 flex gap-1 border-b border-stone-200">
        {TABS.map((tab) => (
          <button
            key={tab.status}
            onClick={() => setStatus(tab.status)}
            className={`px-3 py-2 text-sm transition ${
              status === tab.status
                ? "border-b-2 border-stone-900 font-medium text-stone-900"
                : "text-stone-500 hover:text-stone-800"
            }`}
          >
            {tab.label}
          </button>
        ))}
      </nav>

      {queryStatus === "pending" ? (
        <p className="py-8 text-sm text-stone-500">Loading…</p>
      ) : (data?.items.length ?? 0) === 0 ? (
        <p className="py-8 text-sm text-stone-500">
          {status === "pending_review"
            ? "Nothing waiting for review."
            : "Nothing here."}
        </p>
      ) : (
        <ul className="divide-y divide-stone-200">
          {data?.items.map((item) => (
            <QueueRow key={item.id} item={item} tab={status} />
          ))}
        </ul>
      )}
    </div>
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
    <li className="flex items-start gap-2 py-3">
      <Link
        to={`/console/review/${item.id}`}
        className="min-w-0 flex-1 rounded-lg px-2 py-1 hover:bg-stone-50"
      >
        <div className="flex flex-wrap items-center gap-2">
          <VerdictBadge verdict={item.verdict} size="sm" />
          <span className="text-xs text-stone-500">{item.validationBadge}</span>
          {item.hasWeakEvidence ? (
            <span className="flex items-center gap-1 text-xs text-verdict-weak">
              <AlertTriangle size={12} aria-hidden />
              rests on weak study types
            </span>
          ) : null}
        </div>
        <p className="mt-1 font-medium text-stone-900">{item.headline}</p>
        <p className="text-sm text-stone-500">{item.topic}</p>
        {error ? <p className="mt-1 text-xs text-verdict-weak">{error}</p> : null}
      </Link>

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
          className="mt-1 shrink-0 rounded-lg border border-stone-300 p-1.5 text-stone-500 transition hover:bg-stone-50 hover:text-verdict-weak disabled:opacity-40"
        >
          <Trash2 size={14} aria-hidden />
        </button>
      ) : null}
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
    <form
      onSubmit={onSubmit}
      className="mt-4 rounded-xl border border-stone-200 bg-stone-50 p-3"
    >
      <div className="flex gap-2">
        <input
          value={topic}
          onChange={(event) => setTopic(event.target.value)}
          placeholder="Topic, e.g. ashwagandha for stress"
          className="flex-1 rounded-lg border border-stone-300 px-3 py-1.5 text-sm"
        />
        <button
          type="submit"
          disabled={!topic.trim() || submit.isPending}
          className="rounded-lg bg-stone-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
        >
          {submit.isPending ? "Queueing…" : "Generate draft"}
        </button>
      </div>

      <textarea
        value={blurb}
        onChange={(event) => setBlurb(event.target.value)}
        placeholder="Optional marketing copy — claims are extracted only from what it actually says"
        rows={2}
        className="mt-2 w-full rounded-lg border border-stone-300 px-3 py-1.5 text-sm"
      />

      {submit.isSuccess ? (
        <p className="mt-2 text-xs text-stone-600">
          Queued. The draft will appear here for review once generated — nothing
          publishes without your approval.
        </p>
      ) : null}
      {submit.isError ? (
        <p className="mt-2 text-xs text-verdict-weak">Could not queue that topic.</p>
      ) : null}
    </form>
  );
}
