/**
 * The review screen: editor and sources panel side by side.
 *
 * This is where invariant #1 is actually exercised — the one place in the
 * product where a human decides something goes live. Three things it must
 * never do:
 *
 *   - Offer Approve on anything that is not `pending_review`. The server
 *     enforces this, but showing a button that always 409s trains reviewers to
 *     ignore errors.
 *   - Approve with unsaved edits in flight. Autosave is flushed first, or the
 *     published article is a stale copy of what the reviewer read.
 *   - Collapse a 409 into a generic failure. The detail names which citation
 *     broke, and that is the whole difference between an actionable error and
 *     a scavenger hunt.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";

import { ApiError } from "@/lib/api/client";
import {
  approveArticle,
  consoleKeys,
  fetchArticleDetail,
  rejectArticle,
} from "@/lib/api/console";
import { VerdictBadge } from "@/features/feed/VerdictBadge";
import { ArticleEditor } from "./ArticleEditor";
import { SourcesPanel } from "./SourcesPanel";
import { useAutosave } from "./useAutosave";

export function ReviewDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const autosave = useAutosave(id);

  const [actionError, setActionError] = useState<string | null>(null);
  const [focusedHandle, setFocusedHandle] = useState<string | null>(null);

  const { data: article, status } = useQuery({
    queryKey: consoleKeys.article(id),
    queryFn: () => fetchArticleDetail(id),
    enabled: Boolean(id),
  });

  const approve = useMutation({
    mutationFn: async () => {
      // Publish exactly what the reviewer is looking at.
      await autosave.flush();
      await approveArticle(id);
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["console"] });
      void navigate("/console");
    },
    onError: (error) => setActionError(describeError(error)),
  });

  const reject = useMutation({
    mutationFn: (reason: string) => rejectArticle(id, reason),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["console"] });
      void navigate("/console");
    },
    onError: (error) => setActionError(describeError(error)),
  });

  if (status === "pending") {
    return <p className="p-8 text-sm text-stone-500">Loading draft…</p>;
  }
  if (status === "error" || !article) {
    return <p className="p-8 text-sm text-verdict-weak">Could not load this draft.</p>;
  }

  const canApprove = article.status === "pending_review";
  // Rejection is the only way a draft leaves the queue — there is no delete —
  // so it has to be reachable from validation_failed too, or those drafts have
  // no available action at all. They remain unapprovable.
  const canDiscard = canApprove || article.status === "validation_failed";
  const blockedByUnsaved = autosave.state === "error";

  /**
   * Leaving flushes first. Unmount flushes too, but that fires after the route
   * has already changed — if the save then fails there is no screen left to
   * report it on, and the reviewer walks away believing an edit was kept.
   */
  async function goBack() {
    const saved = await autosave.flush();
    if (!saved) {
      const leave = window.confirm(
        "Some edits could not be saved. Leave anyway and lose them?",
      );
      if (!leave) return;
    }
    void navigate("/console");
  }

  return (
    <div className="flex h-screen flex-col">
      <header className="border-b border-stone-200 px-4 py-3">
        <div className="flex items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            <button
              onClick={() => void goBack()}
              aria-label="Back to review queue"
              className="mt-0.5 shrink-0 rounded-lg border border-stone-300 p-1.5 text-stone-600 transition hover:bg-stone-50 hover:text-stone-900"
            >
              <ArrowLeft size={16} aria-hidden />
            </button>
            <div className="min-w-0">
              <h1 className="truncate font-semibold text-stone-900">
                {article.headline}
              </h1>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <VerdictBadge
                  verdict={article.verdict}
                  qualifier={article.verdictQualifier}
                  size="sm"
                />
                <span className="text-xs text-stone-500">{article.topic}</span>
                {!canApprove ? (
                  <span className="rounded bg-stone-100 px-1.5 py-0.5 text-xs text-stone-600">
                    {article.status.replace("_", " ")}
                  </span>
                ) : null}
              </div>
            </div>
          </div>

          <div className="flex shrink-0 gap-2">
            <button
              onClick={() => {
                setActionError(null);
                const reason = window.prompt(
                  canApprove
                    ? "Reason for rejection (required)"
                    : "Reason for discarding this draft (required)",
                );
                if (reason?.trim()) reject.mutate(reason.trim());
              }}
              disabled={!canDiscard || reject.isPending}
              className="rounded-lg border border-stone-300 px-3 py-1.5 text-sm disabled:opacity-40"
              title={
                canDiscard
                  ? "Removes this draft from the queue. The reason is kept."
                  : "Already resolved"
              }
            >
              {canApprove ? "Reject" : "Discard"}
            </button>
            <button
              onClick={() => {
                setActionError(null);
                approve.mutate();
              }}
              disabled={!canApprove || approve.isPending || blockedByUnsaved}
              className="rounded-lg bg-stone-900 px-3 py-1.5 text-sm text-white disabled:opacity-40"
              title={
                !canApprove
                  ? "Only drafts pending review can be published"
                  : blockedByUnsaved
                    ? "Unsaved edits — resolve the save error first"
                    : "Publish this article"
              }
            >
              {approve.isPending ? "Publishing…" : "Approve & publish"}
            </button>
          </div>
        </div>

        {actionError ? (
          <p
            role="alert"
            className="mt-2 rounded-lg bg-orange-50 px-3 py-2 text-sm text-verdict-weak"
          >
            {actionError}
          </p>
        ) : null}
      </header>

      {/* Editor left, sources right. Side by side is the requirement: the
          reviewer cross-checks one against the other continuously, and a
          tabbed layout turns every check into a context switch. */}
      <div className="grid flex-1 grid-cols-[1fr_380px] overflow-hidden">
        <ArticleEditor
          content={article.editedContent ?? article.originalContent}
          autosave={autosave}
          onCitationClick={setFocusedHandle}
        />
        <SourcesPanel
          sources={article.sources}
          validation={article.validationReport}
          focusedHandle={focusedHandle}
        />
      </div>
    </div>
  );
}

/**
 * Surface the server's detail verbatim where there is one.
 *
 * The 409 from approve says *which* handles no longer resolve; replacing that
 * with "Publish failed" would send the reviewer hunting through the body.
 */
function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    const detail = (error.detail as { detail?: string } | undefined)?.detail;
    if (detail) return detail;
    if (error.status === 409) return "This draft can no longer be published.";
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}
