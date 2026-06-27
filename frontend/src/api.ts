const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "http://localhost:8000";

export type DashboardCounts = {
  transcripts: number;
  projects: number;
  open_tasks: number;
  overdue_tasks: number;
  stalled_tasks: number;
  open_blockers: number;
  open_risks: number;
  decisions: number;
};

export type Task = {
  id: string;
  project_id: string | null;
  transcript_id: string | null;
  title: string;
  description: string | null;
  owner: string | null;
  due_date: string | null;
  status: string;
  priority: string;
  source_quote: string | null;
  confidence: number | null;
  linear_issue_url: string | null;
  jira_issue_url: string | null;
  created_at: string;
  last_status_change_at: string;
};

export type Decision = {
  id: string;
  project_id: string | null;
  transcript_id: string | null;
  summary: string;
  rationale: string | null;
  decided_by: string[] | null;
  source_quote: string | null;
  confidence: number | null;
  created_at: string;
};

export type Project = {
  id: string;
  name: string;
  slug: string;
  description: string | null;
  status: string;
  notion_page_id: string | null;
  linear_project_id: string | null;
};

export type TranscriptSummary = {
  id: string;
  title: string;
  status: string;
  source: string;
  project_id: string | null;
  meeting_date: string | null;
  processed_at: string | null;
  created_at: string;
};

export type DriftItem = {
  kind: "overdue" | "stalled" | "missing_owner" | "unresolved_blocker";
  severity: "info" | "warning" | "critical";
  task_id: string | null;
  blocker_id: string | null;
  project_id: string | null;
  title: string;
  detail: string;
};

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    const text = await response.text();
    throw new Error(`${response.status} ${response.statusText}: ${text}`);
  }
  return (await response.json()) as T;
}

export const api = {
  dashboard: () => request<DashboardCounts>("/api/intelligence/dashboard"),
  projects: () => request<Project[]>("/api/projects"),
  tasks: (params: { status?: string; overdue?: boolean } = {}) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.overdue) search.set("overdue", "true");
    const qs = search.toString();
    return request<{ items: Task[]; total: number }>(
      `/api/tasks${qs ? `?${qs}` : ""}`,
    );
  },
  updateTask: (id: string, payload: Partial<Pick<Task, "status" | "owner" | "priority">>) =>
    request<Task>(`/api/tasks/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  decisions: () => request<{ items: Decision[]; total: number }>("/api/decisions"),
  transcripts: () =>
    request<{ items: TranscriptSummary[]; total: number }>("/api/transcripts"),
  ingest: (payload: {
    title: string;
    content: string;
    project_hint?: string;
    participants?: string[];
  }) =>
    request<{ id: string }>("/api/transcripts", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  ingestVideo: (params: {
    file: File;
    title?: string;
    project_hint?: string;
    participants?: string;
  }) => {
    const form = new FormData();
    form.append("file", params.file);
    form.append("title", params.title ?? "");
    form.append("project_hint", params.project_hint ?? "");
    form.append("participants", params.participants ?? "");
    return fetch(`${API_BASE}/api/transcripts/upload`, {
      method: "POST",
      body: form,
      // No Content-Type header — browser sets multipart boundary automatically
    }).then(async (res) => {
      if (!res.ok) {
        const text = await res.text();
        throw new Error(`${res.status} ${res.statusText}: ${text}`);
      }
      return res.json() as Promise<{ id: string }>;
    });
  },
  runDrift: (notify = false) =>
    request<{ generated_at: string; items: DriftItem[] }>(
      `/api/intelligence/drift/run?notify=${notify}`,
      { method: "POST" },
    ),
  search: (query: string, limit = 5) =>
    request<
      {
        chunk_id: string;
        transcript_id: string;
        transcript_title: string;
        content: string;
        score: number;
      }[]
    >("/api/intelligence/search", {
      method: "POST",
      body: JSON.stringify({ query, limit }),
    }),
};
