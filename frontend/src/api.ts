const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ?? (import.meta.env.DEV ? "http://localhost:8000" : "");

// Keep the deployment's API key in memory, never in built assets or persistent storage.
let apiKey = "";
let bearerToken = "";
let workspaceId = "";
export function setBearerToken(value: string) { bearerToken = value; }
export function setWorkspace(value: string) { workspaceId = value; }
export function setApiKey(value: string) {
  apiKey = value.trim();
}
function authHeaders(): Record<string, string> {
  return { ...(bearerToken ? { Authorization: `Bearer ${bearerToken}` } : apiKey ? { "X-API-Key": apiKey } : {}), ...(workspaceId ? { "X-Workspace-ID": workspaceId } : {}) };
}

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
  salesforce_task_url: string | null;
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

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
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
  waitForExtraction: async (id: string) => {
    for (let attempt = 0; attempt < 120; attempt++) {
      const result = await request<{ status: string }>(`/api/transcripts/${id}/status`);
      if (result.status === "completed") return;
      if (result.status === "failed") throw new Error("Extraction failed. Your meeting is saved; contact your workspace administrator.");
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
    throw new Error("Your meeting is saved and still processing. Check the dashboard before uploading again.");
  },
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
      headers: authHeaders(),
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
