/**
 * The single HTTP boundary. Framework-agnostic on purpose.
 *
 * Nothing in this directory imports from react-router, and nothing imports
 * react. That is what keeps the Next.js port mechanical (DESIGN.md §3.1): this
 * layer runs unchanged in a server component, a loader, or a client hook. The
 * moment a router hook or a `window` reference lands here, that stops being
 * true — and it stops being true quietly.
 */

const API_BASE = "/api";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/** Console auth token. Injected rather than read from storage here, so this
 *  module stays free of browser globals — see the module docstring. */
let authToken: string | null = null;

export function setAuthToken(token: string | null): void {
  authToken = token;
}

export async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (authToken) headers.set("Authorization", `Bearer ${authToken}`);

  const response = await fetch(`${API_BASE}${path}`, { ...init, headers });

  if (response.status === 204) return undefined as T;

  if (!response.ok) {
    // A 409 from approve carries which citations broke — surfacing the detail
    // is the difference between "publish failed" and "citation S3 was deleted
    // from the body". Never collapse it into a generic message.
    const detail = await response.json().catch(() => undefined);
    throw new ApiError(response.status, `Request failed: ${response.status}`, detail);
  }

  return (await response.json()) as T;
}

/** Build a query string, dropping null/undefined. */
export function qs(params: Record<string, string | number | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== null && value !== undefined) search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}
