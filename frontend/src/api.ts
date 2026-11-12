const API_BASE =
  (import.meta.env.VITE_API_URL as string | undefined) ?? "";

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

export type SummaryEvidence = {
  id: string;
  project_id: string | null;
  project_name: string | null;
  transcript_id: string | null;
  source_quote: string | null;
  confidence: number | null;
};

export type WeeklySummary = {
  generated_at: string;
  period_start: string;
  period_end: string;
  open_risk_count: number;
  overdue_task_count: number;
  decision_count: number;
  open_risks: (SummaryEvidence & {
    title: string;
    description: string | null;
    severity: string;
    mitigation: string | null;
  })[];
  overdue_tasks: (SummaryEvidence & {
    title: string;
    owner: string | null;
    priority: string;
    status: string;
    due_date: string;
  })[];
  decisions: (SummaryEvidence & {
    summary: string;
    rationale: string | null;
    decided_by: string[] | null;
    created_at: string;
  })[];
};

export type Task = {
  id: string;
  project_id: string | null;
  transcript_id: string | null;
  title: string;
  description: string | null;
  source: "local" | "meeting" | "jira" | "linear";
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
  sync_pending: boolean;
  sync_checked_at: string | null;
  sync_error: string | null;
};

export type TaskInput = {
  title: string;
  description: string | null;
  owner: string | null;
  due_date: string | null;
  priority: string;
};
export type TaskSyncStatus = {
  providers: { provider: "jira" | "linear"; connected: boolean; last_synced_at: string | null;
    last_attempt_at: string | null; has_more: boolean; error: string | null }[];
  interval_seconds: number;
  status_sync_enabled: boolean;
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
  has_roadmap?: boolean;
};

export type RoadmapMilestone = {
  title: string; objective: string; start_week: number; end_week: number;
  owner: string | null; deliverables: string[]; depends_on: number[];
  status: "planned" | "in_progress" | "blocked" | "done"; task_ids: string[];
};
export type RoadmapDocument = {
  summary: string; assumptions: string[]; milestones: RoadmapMilestone[];
  horizon_weeks: number; start_date: string | null;
  source_type: "manual" | "project" | "transcript"; source_plan: string;
  source_meeting_ids: string[]; generated_at: string;
};
export type RoadmapInput = {
  name: string; project_id: string | null; transcript_id: string | null;
  source_type: RoadmapDocument["source_type"]; plan: string;
  horizon_weeks: number; start_date: string | null;
};
export type ProjectRoadmap = { project_id: string; name: string; version: number; roadmap: RoadmapDocument | null; created_task_count?: number };

export type AoiMeeting = {
  id:string; title:string; kind:"meeting"|"huddle"; starts_at:string; ends_at:string; time_zone:string;
  description:string; attendees:string[]; participants:number|null; calendar_url:string|null; invitation_status:string; calls_enabled?:boolean;
};
export type ScheduleMeetingInput = {
  request_id: string; title: string; meeting_date: string; start_time: string; time_zone: string;
  duration_minutes: number; attendees: string[]; description: string; reminder_minutes: number | null;
};

export type TranscriptSummary = {
  id: string;
  title: string;
  status: string;
  source: string;
  transcription_language?: string;
  sensitivity_label?: "public" | "internal" | "confidential";
  project_id: string | null;
  meeting_date: string | null;
  processed_at: string | null;
  created_at: string;
};

export type TranscriptDetail = TranscriptSummary & {
  content: string;
  segments?: {text: string; confidence: number | null; needs_review: boolean}[];
  participants: string[] | null;
  error: string | null;
  tasks: Pick<Task, "id" | "title" | "owner" | "due_date" | "status" | "priority" | "source_quote" | "confidence">[];
  decisions: Pick<Decision, "id" | "summary" | "rationale" | "decided_by" | "source_quote">[];
  risks: { id: string; title: string; severity: string; likelihood: string; mitigation: string | null; source_quote: string | null }[];
  blockers: { id: string; summary: string; blocked_party: string | null; needs_from: string | null; severity: string; status: string }[];
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
  callStatus: (signal?:AbortSignal)=>request<{calls_enabled:boolean;calendar_enabled:boolean}>("/api/meetings/status",{signal}),
  aoiMeetings: (kind:"meeting"|"huddle",offset=0,signal?:AbortSignal)=>request<{items:AoiMeeting[];has_more:boolean;calls_enabled:boolean;calendar_enabled:boolean}>(`/api/meetings?kind=${kind}&offset=${offset}`,{signal}),
  aoiMeeting: (id:string,signal?:AbortSignal)=>request<AoiMeeting>(`/api/meetings/${encodeURIComponent(id)}`,{signal}),
  startAoiMeeting: (payload:{request_id:string;title:string;kind:"meeting"|"huddle"})=>request<AoiMeeting>("/api/meetings",{method:"POST",body:JSON.stringify(payload)}),
  scheduleAoiMeeting: (payload:ScheduleMeetingInput & {invite_on_calendar:boolean})=>request<AoiMeeting>("/api/meetings/schedule",{method:"POST",body:JSON.stringify(payload),signal:AbortSignal.timeout(45000)}),
  retryMeetingInvitations: (id:string)=>request<AoiMeeting>(`/api/meetings/${encodeURIComponent(id)}/invitations`,{method:"POST",signal:AbortSignal.timeout(45000)}),
  joinAoiMeeting: (id:string,display_name:string)=>request<{token:string;server_url:string}>(`/api/meetings/${encodeURIComponent(id)}/join`,{method:"POST",body:JSON.stringify({display_name}),signal:AbortSignal.timeout(20000)}),
  waitForExtraction: async (id: string) => {
    for (let attempt = 0; attempt < 120; attempt++) {
      const result = await request<{ status: string }>(`/api/transcripts/${id}/status`);
      if (result.status === "completed") return;
      if (result.status === "failed") throw new Error("Extraction failed. Your meeting is saved; contact your workspace administrator.");
      await new Promise(resolve => setTimeout(resolve, 5000));
    }
    throw new Error("Your meeting is saved and still processing. Check the dashboard before uploading again.");
  },
  dashboard: (signal?: AbortSignal) => request<DashboardCounts>("/api/intelligence/dashboard", { signal }),
  weeklySummary: (signal?: AbortSignal) =>
    request<WeeklySummary>("/api/intelligence/weekly-summary", { signal }),
  projects: (signal?: AbortSignal) => request<Project[]>("/api/projects", { signal }),
  projectRoadmap: (id: string, signal?: AbortSignal) => request<ProjectRoadmap>(`/api/projects/${encodeURIComponent(id)}/roadmap`, { signal }),
  generateRoadmap: (payload: RoadmapInput, signal?: AbortSignal) => request<RoadmapDocument>("/api/projects/roadmap/generate", { method: "POST", body: JSON.stringify(payload), signal }),
  saveRoadmap: (payload: { project_id: string | null; name: string; expected_version: number; roadmap: RoadmapDocument; create_tasks: boolean }) => request<ProjectRoadmap>("/api/projects/roadmap", { method: "POST", body: JSON.stringify(payload) }),
  tasks: (params: { status?: string; overdue?: boolean; source?: string; q?: string; limit?: number; offset?: number } = {}, signal?: AbortSignal) => {
    const search = new URLSearchParams();
    if (params.status) search.set("status", params.status);
    if (params.overdue) search.set("overdue", "true");
    if (params.source) search.set("source", params.source);
    if (params.q) search.set("q", params.q);
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    if (params.offset !== undefined) search.set("offset", String(params.offset));
    const qs = search.toString();
    return request<{ items: Task[]; total: number }>(
      `/api/tasks${qs ? `?${qs}` : ""}`, { signal },
    );
  },
  createTask: (payload: TaskInput) => request<Task>("/api/tasks", { method: "POST", body: JSON.stringify(payload) }),
  taskSyncStatus: (signal?: AbortSignal) => request<TaskSyncStatus>("/api/tasks/sync", { signal }),
  syncTasks: () => request<TaskSyncStatus & { results: { provider: string; created: number; updated: number; busy?: boolean; error?: string }[] }>("/api/tasks/sync", { method: "POST", signal: AbortSignal.timeout(90000) }),
  updateTask: (id: string, payload: Partial<TaskInput & Pick<Task, "status">>) =>
    request<Task>(`/api/tasks/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  decisions: () => request<{ items: Decision[]; total: number }>("/api/decisions"),
  transcripts: (params: { limit?: number; offset?: number; q?: string; status?: string; days?: number } = {}, signal?: AbortSignal) => {
    const search = new URLSearchParams();
    if (params.limit !== undefined) search.set("limit", String(params.limit));
    if (params.offset !== undefined) search.set("offset", String(params.offset));
    if (params.q) search.set("q", params.q);
    if (params.status) search.set("status", params.status);
    if (params.days) search.set("days", String(params.days));
    return request<{ items: TranscriptSummary[]; total: number }>(`/api/transcripts?${search}`, { signal });
  },
  transcript: (id: string, signal?: AbortSignal) =>
    request<TranscriptDetail>(`/api/transcripts/${encodeURIComponent(id)}`, { signal }),
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
