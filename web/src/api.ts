import type { ClientConfig, History, IngestJob, Insights, Review, Stats } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) {
    super(message);
  }
}

type TokenProvider = () => Promise<string | null>;
let tokenProvider: TokenProvider = async () => null;

export function setTokenProvider(provider: TokenProvider) {
  tokenProvider = provider;
}

async function request<T>(path: string, init: RequestInit = {}): Promise<{ status: number; body: T }> {
  const token = await tokenProvider();
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "network", "Cannot reach the API. Check your connection and try again.");
  }
  const text = await response.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = null;
  }
  if (!response.ok) {
    const data = (body ?? {}) as { error?: string; detail?: unknown };
    const detail = typeof data.detail === "string" ? data.detail : response.statusText || "Request failed";
    throw new ApiError(response.status, data.error ?? String(response.status), detail);
  }
  return { status: response.status, body: body as T };
}

export const api = {
  config: () => request<ClientConfig>("/v1/config").then((r) => r.body),
  me: () => request<{ uid: string; email: string | null; admin: boolean }>("/v1/me").then((r) => r.body),
  submit: (filename: string, content: string, language?: string) =>
    request<Review>("/v1/reviews", {
      method: "POST",
      body: JSON.stringify({ filename, content, language: language || null }),
    }),
  review: (id: string) => request<Review>(`/v1/reviews/${encodeURIComponent(id)}`).then((r) => r.body),
  retry: (id: string) =>
    request<Review>(`/v1/reviews/${encodeURIComponent(id)}:retry`, { method: "POST" }).then((r) => r.body),
  history: () => request<History>("/v1/history").then((r) => r.body),
  stats: (days: number) =>
    request<{ me: Stats; system?: Stats }>(`/v1/stats?days=${days}`).then((r) => r.body),
  insights: (days: number) => request<Insights>(`/v1/insights?days=${days}`).then((r) => r.body),
  ingest: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<IngestJob>("/admin/rules:ingest", { method: "POST", body: form }).then((r) => r.body);
  },
  ingestJob: (id: string) =>
    request<IngestJob>(`/admin/rules/ingest-jobs/${encodeURIComponent(id)}`).then((r) => r.body),
  corpus: () =>
    request<{ version: string; rule_count: number; label: string; short: string | null }>("/admin/rules/corpus").then((r) => r.body),
};

/** Poll until `done(value)` or the attempt budget runs out; delays grow gently. */
export async function poll<T>(fn: () => Promise<T>, done: (value: T) => boolean, onTick?: (value: T) => void) {
  let delay = 1200;
  for (let attempt = 0; attempt < 240; attempt++) {
    const value = await fn();
    onTick?.(value);
    if (done(value)) return value;
    await new Promise((resolve) => setTimeout(resolve, delay));
    delay = Math.min(5000, Math.round(delay * 1.25));
  }
  throw new ApiError(0, "timeout", "Still working after several minutes — check back from History.");
}
