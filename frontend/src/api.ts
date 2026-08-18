import type {
  AnglesResponse,
  AuditReport,
  DocumentView,
  PosterResponse,
  RunStatus,
  Suggestion,
  TemplateInfo,
  Term,
} from "./types";

const BASE = "/api";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, init);
  if (!resp.ok) {
    let detail = resp.statusText;
    try {
      detail = (await resp.json()).detail ?? detail;
    } catch {
      /* keep statusText */
    }
    throw new Error(detail);
  }
  return resp.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ project_id: string; filename: string; clauses: number }>(
      "/projects",
      { method: "POST", body: form },
    );
  },
  terms: (pid: string) => request<Term[]>(`/projects/${pid}/terms`),
  addTerm: (pid: string, term: string, category: string) =>
    request<{ placeholder: string }>(`/projects/${pid}/terms`, json({ term, category })),
  previewTerm: (pid: string, term: string, category: string) =>
    request<{ occurrences: number }>(
      `/projects/${pid}/terms/preview`,
      json({ term, category }),
    ),
  removeTerm: (pid: string, placeholder: string) =>
    request<{ removed: string }>(`/projects/${pid}/terms/${encodeURIComponent(placeholder)}`, {
      method: "DELETE",
    }),
  suggestions: (pid: string) =>
    request<{ suggestions: Suggestion[]; unreviewed_high_confidence: number }>(
      `/projects/${pid}/suggestions`,
    ),
  dismissSuggestion: (pid: string, surfaces: string[]) =>
    request(`/projects/${pid}/suggestions/dismiss`, json({ surfaces })),
  document: (pid: string) => request<DocumentView>(`/projects/${pid}/document`),
  audit: (pid: string) => request<AuditReport>(`/projects/${pid}/audit`),
  acknowledge: (pid: string, surfaces: string[]) =>
    request(`/projects/${pid}/audit/acknowledge`, json({ surfaces })),
  buildIndex: (pid: string) =>
    request<{ chunks: number; validated: boolean; embedding_source: string }>(
      `/projects/${pid}/index`,
      { method: "POST" },
    ),
  angles: (pid: string) => request<AnglesResponse>(`/projects/${pid}/angles`),
  templates: () => request<TemplateInfo[]>("/templates"),
  startRun: (pid: string, angle: string, template_family: string) =>
    request<{ run_id: string }>(`/projects/${pid}/runs`, json({ angle, template_family })),
  runStatus: (runId: string) => request<RunStatus>(`/runs/${runId}`),
  resume: (runId: string, from_node: string, edits: Record<string, unknown> = {}) =>
    request(`/runs/${runId}/resume`, json({ from_node, edits })),
  poster: (runId: string) => request<PosterResponse>(`/runs/${runId}/poster`),
  exportUrl: (runId: string, orientation: string) =>
    `${BASE}/runs/${runId}/exports/${orientation}.pptx`,
};
