import { useState } from "react";
import {
  api,
  type DashboardCounts,
  type Task,
  type TranscriptSummary,
} from "../api";
import type { Navigate } from "../navigation";
import { usePollingData } from "../usePollingData";
import { Icon } from "./Icon";
import { MeetingList } from "./Meetings";

async function loadOverview(signal: AbortSignal) {
  const [counts, meetings, tasks] = await Promise.all([
    api.dashboard(signal),
    api.transcripts({ limit: 6 }, signal),
    api.tasks({ status: "open" }, signal).catch(() => null),
  ]);
  return { counts, meetings, tasks };
}

const exampleMeetings: TranscriptSummary[] = [
  {
    id: "example-1",
    title: "Product & engineering sync",
    source: "live",
    status: "completed",
    project_id: null,
    meeting_date: "2026-10-23T17:00:00Z",
    processed_at: null,
    created_at: "2026-10-23T17:00:00Z",
  },
  {
    id: "example-2",
    title: "A better first week for customers",
    source: "upload",
    status: "completed",
    project_id: null,
    meeting_date: "2026-10-22T20:30:00Z",
    processed_at: null,
    created_at: "2026-10-22T20:30:00Z",
  },
  {
    id: "example-3",
    title: "The next chapter: roadmap planning",
    source: "live",
    status: "completed",
    project_id: null,
    meeting_date: "2026-10-22T17:00:00Z",
    processed_at: null,
    created_at: "2026-10-22T17:00:00Z",
  },
  {
    id: "example-4",
    title: "Design meets engineering",
    source: "upload",
    status: "completed",
    project_id: null,
    meeting_date: "2026-10-21T18:00:00Z",
    processed_at: null,
    created_at: "2026-10-21T18:00:00Z",
  },
];
const exampleCounts: DashboardCounts = {
  transcripts: 4,
  projects: 2,
  open_tasks: 7,
  overdue_tasks: 2,
  stalled_tasks: 1,
  open_blockers: 1,
  open_risks: 0,
  decisions: 5,
};
const exampleTasks = [
  {
    id: "example-task-1",
    title: "Share the onboarding prototype",
    owner: "Alex",
    due_date: "2026-10-26T17:00:00Z",
    priority: "high",
  },
  {
    id: "example-task-2",
    title: "Confirm the release checklist",
    owner: "Sam",
    due_date: "2026-10-27T17:00:00Z",
    priority: "medium",
  },
  {
    id: "example-task-3",
    title: "Review customer feedback",
    owner: "Jordan",
    due_date: null,
    priority: "medium",
  },
];
type TaskPreview = Pick<
  Task,
  "id" | "title" | "owner" | "due_date" | "priority"
>;

export function Dashboard({ onNavigate }: { onNavigate: Navigate }) {
  const { data, error, loading, refresh } = usePollingData(loadOverview);
  const [example, setExample] = useState(false);
  const [filter, setFilter] = useState("all");
  const [selectedExample, setSelectedExample] = useState<string | null>(null);
  const counts = example ? exampleCounts : data?.counts;
  const meetings = example ? exampleMeetings : (data?.meetings.items ?? []);
  const tasks: TaskPreview[] | undefined = example
    ? exampleTasks
    : data?.tasks?.items.slice(0, 3);
  const filtered = meetings.filter(
    (meeting) => filter === "all" || meeting.status === filter,
  );
  const attention = [
    {
      label: "Overdue tasks",
      value: counts?.overdue_tasks,
      icon: "clock" as const,
    },
    {
      label: "Stalled tasks",
      value: counts?.stalled_tasks,
      icon: "tasks" as const,
    },
    {
      label: "Open blockers",
      value: counts?.open_blockers,
      icon: "alert" as const,
    },
    {
      label: "Open risks",
      value: counts?.open_risks,
      icon: "intelligence" as const,
    },
  ].filter((item) => (item.value ?? 0) > 0);
  const hasRail =
    attention.length > 0 || !!tasks?.length || !!counts?.open_tasks;
  const hasCounts = counts && Object.values(counts).some((value) => value > 0);
  const allNotes = () => onNavigate("meetings", undefined, "captured");
  return (
    <div className="workspace-page dashboard-studio simple-home">
      <header className="dashboard-welcome">
        <div className="welcome-copy">
          <h2>Your workspace.</h2>
          <p>Meetings, tasks, and plans in one place.</p>
        </div>
        <div className="welcome-actions">
          <button className="primary" onClick={() => onNavigate("meetings")}>
            <Icon name="meetings" />
            Open meetings
          </button>
          <button className="ghost" onClick={() => onNavigate("upload")}>
            <Icon name="upload" />
            Upload transcript
          </button>
        </div>
      </header>
      {example && (
        <div className="example-banner" role="status">
          <Icon name="book" />
          <span>Example data. Your workspace hasn’t changed.</span>
          <button
            className="text-button"
            onClick={() => {
              setExample(false);
              setSelectedExample(null);
              setFilter("all");
            }}
          >
            Exit preview
            <Icon name="close" />
          </button>
        </div>
      )}
      {error && (
        <div className="connection-notice" role="alert">
          <Icon name="link" />
          <div>
            <strong>
              {data ? "Updates paused." : "Couldn’t connect to your workspace."}
            </strong>
            <span>
              {data
                ? "Showing the last loaded records."
                : "Check the backend connection and try again."}
            </span>
          </div>
          <button className="text-button" onClick={refresh} disabled={loading}>
            Try again
          </button>
        </div>
      )}
      <div
        className={`dashboard-work-grid simple-work-grid${hasRail ? "" : " no-rail"}`}
      >
        <section className="meeting-section meeting-ledger">
          <div className="section-heading">
            <h3>Recent meeting notes</h3>
            <button className="text-button" onClick={allNotes}>
              All notes
              <Icon name="arrow" />
            </button>
          </div>
          {meetings.length > 0 && (
            <div className="meeting-ledger-controls">
              <div
                className="meeting-filters"
                role="group"
                aria-label="Filter recent meetings"
              >
                {[
                  { id: "all", label: "All" },
                  { id: "live", label: "Live" },
                  { id: "completed", label: "Completed" },
                ].map((item) => (
                  <button
                    key={item.id}
                    aria-pressed={filter === item.id}
                    onClick={() => setFilter(item.id)}
                  >
                    {item.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          {filtered.length ? (
            <MeetingList
              meetings={filtered}
              onOpen={(id) =>
                example
                  ? setSelectedExample((value) => (value === id ? null : id))
                  : onNavigate("meetings", id)
              }
            />
          ) : meetings.length ? (
            <div className="filter-empty">
              <h4>No {filter} meetings.</h4>
              <button className="text-button" onClick={() => setFilter("all")}>
                Show all notes
              </button>
            </div>
          ) : (
            <div className="simple-meeting-empty" aria-busy={loading && !error}>
              <Icon name="summary" />
              <h4>
                {loading && !error
                  ? "Loading meeting notes…"
                  : error
                    ? "Your notes will appear here."
                    : "No meeting notes yet."}
              </h4>
              <p>
                {error
                  ? "Reconnect to see your saved notes."
                  : "Record a meeting or upload a transcript to get started."}
              </p>
              <button
                className="text-button"
                onClick={() => onNavigate("extension")}
              >
                Set up recording
                <Icon name="arrow" />
              </button>
            </div>
          )}
          {selectedExample && example && (
            <div className="example-meeting-detail">
              <div className="section-heading">
                <h4>
                  {
                    exampleMeetings.find(
                      (meeting) => meeting.id === selectedExample,
                    )?.title
                  }
                </h4>
                <button
                  className="icon-action"
                  aria-label="Close example meeting"
                  onClick={() => setSelectedExample(null)}
                >
                  <Icon name="close" />
                </button>
              </div>
              <span className="sample-label">Example meeting notes</span>
              <p>
                <strong>Next step:</strong> Share the onboarding prototype with
                the team.
              </p>
              <p>
                <strong>Decision:</strong> Test the new flow before the next
                release.
              </p>
              <details>
                <summary>View example source quote</summary>
                <blockquote>
                  “I’ll share the prototype on Monday so we can test it before
                  the release.”
                </blockquote>
              </details>
            </div>
          )}
        </section>
        {hasRail && (
          <aside className="dashboard-right-rail">
            {!!attention.length && (
              <section className="attention-section">
                <div className="section-heading">
                  <h3>Needs attention</h3>
                </div>
                <ul>
                  {attention.map((item) => (
                    <li key={item.label}>
                      <button onClick={() => onNavigate("intelligence")}>
                        <Icon name={item.icon} />
                        <span>{item.label}</span>
                        <strong>{item.value}</strong>
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            )}
            {tasks?.length || counts?.open_tasks ? (
              <section className="next-steps-section">
                <div className="section-heading">
                  <h3>Next tasks</h3>
                  <button
                    className="text-button"
                    onClick={() => onNavigate("tasks")}
                  >
                    View all
                  </button>
                </div>
                {tasks?.length ? (
                  <ul>
                    {tasks.map((task) => (
                      <li key={task.id}>
                        <button onClick={() => onNavigate("tasks")}>
                          <span
                            className={`task-priority priority-${task.priority}`}
                            aria-label={`${task.priority} priority`}
                          />
                          <span>
                            <strong>{task.title}</strong>
                            <small>
                              {task.owner ?? "Unassigned"}
                              {task.due_date
                                ? ` · ${new Date(task.due_date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}`
                                : ""}
                            </small>
                          </span>
                          <Icon name="chevron" />
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <button
                    className="text-button"
                    onClick={() => onNavigate("tasks")}
                  >
                    Review your open tasks
                    <Icon name="arrow" />
                  </button>
                )}
              </section>
            ) : null}
          </aside>
        )}
      </div>
      {hasCounts && (
        <section
          className="overview-totals"
          aria-label={example ? "Example workspace totals" : "Workspace totals"}
        >
          <Metric
            label="Meeting notes"
            value={counts?.transcripts}
            icon="meetings"
            onClick={allNotes}
          />
          <Metric
            label="Open tasks"
            value={counts?.open_tasks}
            icon="tasks"
            onClick={() => onNavigate("tasks")}
          />
          <Metric
            label="Decisions"
            value={counts?.decisions}
            icon="decisions"
            onClick={() => onNavigate("decisions")}
          />
        </section>
      )}
      <div className="home-utilities">
        <span className="workspace-status">
          <span />
          {example
            ? "Example workspace"
            : error
              ? "Connection interrupted"
              : loading
                ? "Updating…"
                : "Up to date"}
        </span>
        <button
          className="icon-action"
          aria-label="Refresh home"
          onClick={refresh}
          disabled={loading}
        >
          <Icon name="refresh" />
        </button>
        {!example && (
          <button
            className="text-button sample-toggle"
            onClick={() => {
              setExample(true);
              setSelectedExample(null);
              setFilter("all");
            }}
          >
            Preview example data
          </button>
        )}
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  icon,
  onClick,
}: {
  label: string;
  value?: number;
  icon: "meetings" | "tasks" | "decisions";
  onClick: () => void;
}) {
  return (
    <button className={`overview-total metric-${icon}`} onClick={onClick}>
      <span className="metric-icon">
        <Icon name={icon} />
      </span>
      <span className="metric-copy">
        <span>{label}</span>
        <strong>{value === undefined ? "—" : value.toLocaleString()}</strong>
      </span>
      <Icon name="chevron" />
    </button>
  );
}
