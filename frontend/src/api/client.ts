// Thin, typed client for the Pheme API (proxied at /api by Vite).
import type {
  ChatMessage,
  DashboardSummary,
  DashboardVersion,
  DecisionChoice,
  DraftPlan,
  Measure,
  PatchOp,
  QueryResult,
  SemanticQuery,
  Session,
  Upload,
  VersionMeta,
} from "./types";

export const API = "/api";
export const UNAUTHORIZED = "pheme:unauthorized";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED));
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const data = await res.json();
      detail = typeof data.detail === "string" ? data.detail : JSON.stringify(data.detail);
    } catch {
      // keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  createSession: (dashboardId?: string) =>
    request<Session>("POST", "/sessions", dashboardId ? { dashboard_id: dashboardId } : {}),
  session: (id: string) => request<Session>("GET", `/sessions/${id}`),
  messages: (id: string) => request<ChatMessage[]>("GET", `/sessions/${id}/messages`),
  send: (id: string, text: string, selected: string[] = []) =>
    request<{ reply: string; state: string }>("POST", `/sessions/${id}/messages`, {
      text,
      selected_widget_ids: selected,
    }),
  context: (id: string, text: string) => request<unknown>("POST", `/sessions/${id}/context`, { text }),
  login: (email: string) => request<{ token: string }>("POST", "/auth/login", { email }),
  approve: (id: string) => request<{ state: string }>("POST", `/sessions/${id}/approve`),
  toggleOption: (id: string, optionId: string, included: boolean) =>
    request<Session>("POST", `/sessions/${id}/options/${encodeURIComponent(optionId)}`, { included }),
  applyEdit: (id: string) => request<{ version: number }>("POST", `/sessions/${id}/edit/apply`),
  discardEdit: (id: string) => request<void>("POST", `/sessions/${id}/edit/discard`),
  plan: (planId: string) => request<DraftPlan>("GET", `/plans/${planId}`),
  decide: (decisionId: string, choice: DecisionChoice, detail?: string) =>
    request<DraftPlan>("POST", `/decisions/${decisionId}`, { choice, detail }),
  upload: (id: string) => request<Upload>("GET", `/uploads/${id}`),
  query: (datasetId: string, query: SemanticQuery, measures: Measure[]) =>
    request<QueryResult>("POST", "/query", { dataset_id: datasetId, query, measures }),
  dashboards: (params: Record<string, string> = {}) =>
    request<DashboardSummary[]>("GET", `/dashboards?${new URLSearchParams(params)}`),
  dashboard: (id: string, version?: number) =>
    request<DashboardVersion>("GET", version ? `/dashboards/${id}/versions/${version}` : `/dashboards/${id}`),
  versions: (id: string) => request<VersionMeta[]>("GET", `/dashboards/${id}/versions`),
  data: (id: string, filters: Record<string, string[]> = {}, version?: number) => {
    const qs = new URLSearchParams();
    if (version) qs.set("version", String(version));
    for (const [k, v] of Object.entries(filters)) if (v.length) qs.set(`filter.${k}`, v.join(","));
    return request<Record<string, QueryResult | string>>("GET", `/dashboards/${id}/data?${qs}`);
  },
  diff: (id: string, from: number, to: number) =>
    request<Array<{ op: string; path: string; value?: unknown }>>("GET", `/dashboards/${id}/diff?from=${from}&to=${to}`),
  restore: (id: string, n: number) => request<DashboardVersion>("POST", `/dashboards/${id}/restore/${n}`),
  patch: (id: string, baseVersion: number, ops: PatchOp[], note: string) =>
    request<DashboardVersion>("PATCH", `/dashboards/${id}`, { base_version: baseVersion, ops, note }),
  refresh: (id: string, uploadId: string) =>
    request<{ status: string; mapping?: Record<string, string | null> }>("POST", `/dashboards/${id}/refresh`, {
      upload_id: uploadId,
    }),
};

/** Upload with progress (fetch can't report upload progress). Resolves with the created upload. */
export function uploadFile(
  file: File,
  fields: Record<string, string>,
  onProgress: (fraction: number) => void,
): Promise<Upload> {
  return new Promise((resolve, reject) => {
    const form = new FormData();
    form.append("file", file);
    for (const [k, v] of Object.entries(fields)) form.append(k, v);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API}/uploads`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      if (xhr.status === 401) window.dispatchEvent(new Event(UNAUTHORIZED));
      if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText) as Upload);
      else {
        let detail = xhr.statusText;
        try {
          detail = JSON.parse(xhr.responseText).detail;
        } catch {
          // keep statusText
        }
        reject(new ApiError(xhr.status, detail));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "The upload failed. Check your connection."));
    xhr.send(form);
  });
}

/** Poll an upload until ingestion finishes. */
export async function waitForUpload(id: string, intervalMs = 400, timeoutMs = 120_000): Promise<Upload> {
  const start = Date.now();
  for (;;) {
    const u = await api.upload(id);
    if (u.status === "done" || u.status === "failed") return u;
    if (Date.now() - start > timeoutMs) throw new ApiError(408, "Processing the file is taking too long.");
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}
