/**
 * The review screen: editor and evidence side by side.
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
 *
 * The layout is the page scrolling with the evidence column pinned, rather than
 * two independently scrolling panes. Side by side is the requirement — the
 * reviewer cross-checks one against the other continuously, and a tabbed layout
 * turns every check into a context switch — but a pinned column gets that
 * without trapping the wheel in whichever pane the cursor happens to be over.
 *
 * The decision controls sit at the bottom of that column, under the evidence
 * they follow from. Putting Approve in the top bar, which is the reflex, places
 * it where a reviewer's hand rests *before* they have read anything.
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams } from "react-router-dom";

import { ApiError } from "@/lib/api/client";
import {
  approveArticle,
  consoleKeys,
  fetchArticleDetail,
  rejectArticle,
} from "@/lib/api/console";
import { VerdictMark } from "@/features/evidence/VerdictMark";
import { VERDICT_LABELS } from "@/features/evidence/labels";
import { ArticleEditor } from "./ArticleEditor";
import { SourcesPanel, ValidationSummary } from "./SourcesPanel";
import { useAuth } from "./auth";
import { SECTION_LABEL } from "./controls";
import {
  ACTION_ERROR_ALERT,
  APPROVE_BUTTON,
  BACK_TO_QUEUE,
  DECISION_BLOCK,
  DECISION_NOTE,
  DRAFT_HEADLINE,
  DRAFT_META,
  DRAFT_STATUS_ROW,
  DRAFT_VERDICT_WORDING,
  EDITOR_SLOT,
  REASON_MISSING_ALERT,
  REJECT_BUTTON,
  REJECT_REASON_FIELD,
  REVIEW_BODY_COLUMN,
  REVIEW_COLUMNS,
  REVIEW_PAGE,
  REVIEW_SIDEBAR,
  REVIEW_TOP_BAR,
  SIDEBAR_SOURCES,
  SIGNED_IN_AS,
  SIGN_OUT_ACTION,
  STATUS_DIVIDER,
} from "./styles";
import { ROUTE_ERROR_MESSAGE, ROUTE_MESSAGE } from "@/features/shell/styles";
import { useAutosave } from "./useAutosave";

export function ReviewDetail() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const autosave = useAutosave(id);
  const { reviewer, logout } = useAuth();

  const [actionError, setActionError] = useState<string | null>(null);
  const [focusedHandle, setFocusedHandle] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");
  const [reasonMissing, setReasonMissing] = useState(false);

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
    return (
      <p className={ROUTE_MESSAGE}>Loading draft…</p>
    );
  }
  if (status === "error" || !article) {
    return (
      <p className={ROUTE_ERROR_MESSAGE}>Could not load this draft.</p>
    );
  }

  const canApprove = article.status === "pending_review";
  // Rejection is the only way a draft leaves the queue — there is no delete —
  // so it has to be reachable from validation_failed too, or those drafts have
  // no available action at all. They remain unapprovable.
  const canDiscard = canApprove || article.status === "validation_failed";
  const blockedByUnsaved = autosave.state === "error";
  const restsOnWeakEvidence = article.sources.some((s) => s.wasCited && s.isWeakEvidence);

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

  function onReject() {
    setActionError(null);
    if (!rejectReason.trim()) {
      // Not a server rule — the reason is the single most useful artefact a
      // discarded draft leaves behind, because it is what the synthesis prompt
      // gets fixed from. Blocking here is cheaper than losing it.
      setReasonMissing(true);
      return;
    }
    setReasonMissing(false);
    reject.mutate(rejectReason.trim());
  }

  return (
    <main className={REVIEW_PAGE}>
      <div className={REVIEW_TOP_BAR}>
        <button onClick={() => void goBack()} className={BACK_TO_QUEUE}>
          ← Queue
        </button>
        <span className={SIGNED_IN_AS}>
          Signed in as {reviewer?.displayName ?? "—"} ·
          <button onClick={logout} className={SIGN_OUT_ACTION}>
            Sign out
          </button>
        </span>
      </div>

      <div className={REVIEW_COLUMNS}>
        <section className={REVIEW_BODY_COLUMN}>
          <div className={DRAFT_STATUS_ROW}>
            <span className={SECTION_LABEL}>
              {article.status === "validation_failed"
                ? "Draft · failed validation"
                : article.status === "pending_review"
                  ? "Draft · pending review"
                  : `Draft · ${article.status.replace(/_/g, " ")}`}
            </span>
            <span aria-hidden className={STATUS_DIVIDER} />
            <VerdictMark verdict={article.verdict} size="sm" />
            <span className={DRAFT_VERDICT_WORDING}>
              {VERDICT_LABELS[article.verdict]}
            </span>
            {article.verdictQualifier ? (
              <span className={DRAFT_META}>{article.verdictQualifier}</span>
            ) : null}
            <span className={DRAFT_META}>{article.topic}</span>
          </div>

          {/*
            Read-only. The headline is not part of the autosave contract — that
            endpoint takes a TipTap document and nothing else — so an editable
            field here would silently discard what was typed into it. Making it
            editable is an API change, not a styling one.
          */}
          <h1 className={DRAFT_HEADLINE}>{article.headline}</h1>

          <div className={EDITOR_SLOT}>
            <ArticleEditor
              content={article.editedContent ?? article.originalContent}
              autosave={autosave}
              onCitationClick={setFocusedHandle}
            />
          </div>
        </section>

        <aside className={REVIEW_SIDEBAR}>
          <ValidationSummary
            report={article.validationReport}
            grade={article.evidenceGrade}
            restsOnWeakEvidence={restsOnWeakEvidence}
          />

          <div className={SIDEBAR_SOURCES}>
            <SourcesPanel sources={article.sources} focusedHandle={focusedHandle} />
          </div>

          <div className={DECISION_BLOCK}>
            <span className={SECTION_LABEL}>Decision</span>

            <button
              onClick={() => {
                setActionError(null);
                approve.mutate();
              }}
              disabled={!canApprove || approve.isPending || blockedByUnsaved}
              title={
                !canApprove
                  ? "Only drafts pending review can be published"
                  : blockedByUnsaved
                    ? "Unsaved edits — resolve the save error first"
                    : "Publish this article"
              }
              className={APPROVE_BUTTON}
            >
              {approve.isPending ? "Publishing…" : "Approve and publish"}
            </button>

            <input
              value={rejectReason}
              onChange={(event) => {
                setRejectReason(event.target.value);
                setReasonMissing(false);
              }}
              disabled={!canDiscard}
              aria-label={canApprove ? "Reason for rejecting" : "Reason for discarding"}
              placeholder={
                canApprove
                  ? "Reason for rejecting (required)"
                  : "Reason for discarding (required)"
              }
              className={REJECT_REASON_FIELD}
            />

            <button
              onClick={onReject}
              disabled={!canDiscard || reject.isPending}
              title={
                canDiscard
                  ? "Removes this draft from the queue. The reason is kept."
                  : "Already resolved"
              }
              className={REJECT_BUTTON}
            >
              {canApprove ? "Reject" : "Discard"}
            </button>

            {reasonMissing ? (
              <p role="alert" className={REASON_MISSING_ALERT}>
                A reason is required before a draft can be rejected.
              </p>
            ) : null}

            {actionError ? (
              <p role="alert" className={ACTION_ERROR_ALERT}>
                {actionError}
              </p>
            ) : null}

            <p className={DECISION_NOTE}>
              Approval writes your name, the timestamp and the edited copy. The
              model's original draft is kept unchanged.
            </p>
          </div>
        </aside>
      </div>
    </main>
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
