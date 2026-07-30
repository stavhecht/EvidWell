/**
 * Editorial console data access. Every call requires a bearer token.
 */

import { apiFetch, qs, setAuthToken } from "./client";
import type {
  ArticleDetail,
  ArticleStatus,
  QueueItem,
  Reviewer,
  TipTapDoc,
} from "@/types/api";

export const consoleKeys = {
  queue: (status: ArticleStatus) => ["console", "queue", status] as const,
  article: (id: string) => ["console", "article", id] as const,
  runs: ["console", "runs"] as const,
  me: ["console", "me"] as const,
};

/**
 * Exchange credentials for a bearer token and load the reviewer.
 *
 * The token response keeps OAuth 2's snake_case field names — that shape is a
 * standard, not our house style, so the server does not camelCase it.
 */
export async function login(
  email: string,
  password: string,
): Promise<{ token: string; reviewer: Reviewer }> {
  const response = await apiFetch<{ access_token: string }>("/console/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
  });
  setAuthToken(response.access_token);
  const reviewer = await fetchMe();
  return { token: response.access_token, reviewer };
}

/** Re-validates the stored token as a side effect of loading the reviewer. */
export async function fetchMe(): Promise<Reviewer> {
  return apiFetch<Reviewer>("/console/auth/me");
}

/**
 * The review queue.
 *
 * `status` is a parameter because the console also shows a validation_failed
 * tab. Those drafts can never be approved, but they are how a prompt bug
 * becomes visible — hiding them makes the grounding check look like silence.
 *
 * STUB.
 */
export async function fetchQueue(params: {
  status?: ArticleStatus;
  cursor?: string;
  limit?: number;
}): Promise<{ items: QueueItem[]; nextCursor: string | null }> {
  return apiFetch(`/console/articles${qs(params)}`);
}

/** Draft + sources + validation report, in one request. STUB. */
export async function fetchArticleDetail(id: string): Promise<ArticleDetail> {
  return apiFetch<ArticleDetail>(`/console/articles/${id}`);
}

/**
 * Autosave into edited_content. Debounced ~800ms by the caller.
 *
 * Only ever sends edited content. original_content is not part of this
 * payload and is rejected server-side by a DB trigger if anything tries.
 *
 * STUB.
 */
export async function saveContent(id: string, content: TipTapDoc): Promise<void> {
  return apiFetch<void>(`/console/articles/${id}/content`, {
    method: "PATCH",
    body: JSON.stringify({ content }),
  });
}

/**
 * Publish. The only path to `published` in the system.
 *
 * Can fail with 409 when editing has orphaned a citation — the error detail
 * names the specific handles, and the UI must show them rather than a generic
 * failure.
 *
 * STUB.
 */
export async function approveArticle(id: string): Promise<void> {
  return apiFetch<void>(`/console/articles/${id}/approve`, { method: "POST" });
}

/** Reject with a required reason. Terminal. STUB. */
export async function rejectArticle(id: string, reason: string): Promise<void> {
  return apiFetch<void>(`/console/articles/${id}/reject`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

/** Enqueue a topic. Returns 202 — generation takes minutes. STUB. */
export async function createRun(topic: string, blurb?: string): Promise<{ id: string }> {
  return apiFetch(`/console/pipeline/runs`, {
    method: "POST",
    body: JSON.stringify({ topic, blurb }),
  });
}
