import { useState } from "react";
import { api, type DashboardCounts, type Task, type TranscriptSummary } from "../api";
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
  { id: "example-1", title: "Product & engineering sync", source: "live", status: "completed", project_id: null, meeting_date: "2026-10-23T17:00:00Z", processed_at: null, created_at: "2026-10-23T17:00:00Z" },
  { id: "example-2", title: "A better first week for customers", source: "upload", status: "completed", project_id: null, meeting_date: "2026-10-22T20:30:00Z", processed_at: null, created_at: "2026-10-22T20:30:00Z" },
  { id: "example-3", title: "The next chapter: roadmap planning", source: "live", status: "completed", project_id: null, meeting_date: "2026-10-22T17:00:00Z", processed_at: null, created_at: "2026-10-22T17:00:00Z" },
  { id: "example-4", title: "Design meets engineering", source: "upload", status: "completed", project_id: null, meeting_date: "2026-10-21T18:00:00Z", processed_at: null, created_at: "2026-10-21T18:00:00Z" },
];
const exampleCounts: DashboardCounts = { transcripts: 4, projects: 2, open_tasks: 7, overdue_tasks: 2, stalled_tasks: 1, open_blockers: 1, open_risks: 0, decisions: 5 };
const exampleTasks = [
  { id: "example-task-1", title: "Share the onboarding prototype", owner: "Alex", due_date: "2026-10-26T17:00:00Z", priority: "high" },
  { id: "example-task-2", title: "Confirm the release checklist", owner: "Sam", due_date: "2026-10-27T17:00:00Z", priority: "medium" },
  { id: "example-task-3", title: "Review customer feedback", owner: "Jordan", due_date: null, priority: "medium" },
];
type TaskPreview = Pick<Task, "id" | "title" | "owner" | "due_date" | "priority">;

export function Dashboard({ onNavigate }: { onNavigate: Navigate }) {
  const { data, error, loading, refresh } = usePollingData(loadOverview);
  const [example, setExample] = useState(false);
  const [filter, setFilter] = useState("all");
  const [selectedExample, setSelectedExample] = useState<string | null>(null);
  const counts = example ? exampleCounts : data?.counts;
  const meetings = example ? exampleMeetings : data?.meetings.items ?? [];
  const tasks: TaskPreview[] | undefined = example ? exampleTasks : data?.tasks?.items.slice(0, 3);
  const filtered = meetings.filter(meeting => filter === "all" || meeting.status === filter);
  const isOffline = !!error && !data && !example;

  return <div className="workspace-page dashboard-studio">
    <div className="overview-toolbar"><span className={`workspace-status${isOffline ? " is-offline" : ""}`}><span/>{example ? "Example workspace" : error ? "Connection interrupted" : loading && !data ? "Connecting to your workspace" : "Your workspace, up to date"}</span><div><button className="text-button sample-toggle" onClick={() => { setExample(value => !value); setSelectedExample(null); setFilter("all"); }}>{example ? "Back to my workspace" : "Explore an example"}</button><button className="icon-action refresh-overview" aria-label="Refresh" onClick={refresh} disabled={loading}><Icon name="refresh"/></button></div></div>
    {example && <div className="example-banner" role="status"><Icon name="book"/><span>You’re exploring example data. Your workspace hasn’t changed.</span><button className="text-button" onClick={() => { setExample(false); setSelectedExample(null); }}>Exit preview<Icon name="close"/></button></div>}
    <header className="dashboard-welcome">
      <div className="welcome-copy">
        <h2>Less admin.<br/><em>More momentum.</em></h2>
        <p>Good conversations deserve great follow-through.<br className="desktop-break"/> Here’s where your meetings become next steps.</p>
        <div className="welcome-actions"><button className="primary" onClick={() => onNavigate("upload")}><Icon name="plus"/>Upload transcript</button><button className="welcome-secondary" onClick={() => onNavigate("meetings")}>View meetings<Icon name="arrow"/></button></div>
      </div>
      <section className="capture-callout" aria-label="Capture with desktop or browser apps">
        <div className="capture-visual" aria-hidden="true">
          <div className="capture-signal">{Array.from({ length: 27 }, (_, index) => <i key={index} style={{ height: `${5 + Math.abs(Math.sin(index * .79) * Math.cos(index * .18)) * 25}px` }}/>)}</div>
        </div>
        <div className="capture-callout-copy"><h3>Be there. We’ll take it from here.</h3><p>Live audio. Clear notes. Every next step, captured.</p></div>
        <button className="capture-button" onClick={() => onNavigate("extension")}><span>Set up recording</span><Icon name="arrow"/></button>
      </section>
    </header>
    {isOffline && <div className="connection-notice" role="alert"><Icon name="link"/><div><strong>Your workspace is waiting to connect.</strong><span>Check your backend connection to load meetings, or explore an example.</span></div><button className="text-button" onClick={refresh} disabled={loading}>Try again<Icon name="refresh"/></button></div>}
    {error && data && !example && <div className="connection-notice" role="alert">Updates paused. Showing the last loaded workspace.<button className="text-button" onClick={refresh}>Try again</button></div>}
    <section className="overview-totals" aria-label={example ? "Example workspace totals" : "Workspace totals"}>
      <Metric label="Meetings captured" value={counts?.transcripts} icon="meetings" onClick={() => onNavigate("meetings")}/>
      <Metric label="Open tasks" value={counts?.open_tasks} icon="tasks" onClick={() => onNavigate("tasks")}/>
      <Metric label="Decisions made" value={counts?.decisions} icon="decisions" onClick={() => onNavigate("decisions")}/>
      <div className="metric-projects"><span className="project-shapes" aria-hidden="true"><Icon name="book"/></span><div><strong>{counts ? `${counts.projects} project${counts.projects === 1 ? "" : "s"}` : "Your projects"}</strong><span>One shared memory.</span></div></div>
    </section>
    <div className="dashboard-work-grid">
      <section className="meeting-section meeting-ledger"><div className="section-heading"><h3>Recent meetings<span className="heading-count">{example ? "Example" : data?.meetings.total ?? ""}</span></h3><button className="text-button" onClick={() => onNavigate("meetings")}>View all<Icon name="arrow"/></button></div><div className="meeting-ledger-controls"><div className="meeting-filters" role="group" aria-label="Filter recent meetings">{[{ id: "all", label: "All meetings" }, { id: "live", label: "Live" }, { id: "completed", label: "Completed" }].map(item => <button key={item.id} aria-pressed={filter === item.id} onClick={() => setFilter(item.id)}>{item.id === "live" && <span className="filter-live-dot"/>}{item.label}</button>)}</div><span className="ledger-caption"><Icon name="clock"/>Most recent first</span></div>
        {filtered.length ? <MeetingList meetings={filtered} onOpen={id => example ? setSelectedExample(value => value === id ? null : id) : onNavigate("meetings", id)}/> : meetings.length ? <div className="filter-empty"><Icon name="meetings"/><h4>{filter === "live" ? "No meetings live right now." : "No completed meetings yet."}</h4><p>{filter === "live" ? "Your next capture will appear here as it happens." : "Finalized meeting notes will appear here."}</p><button className="text-button" onClick={() => setFilter("all")}>Show all meetings<Icon name="arrow"/></button></div> : <MeetingEmpty loading={loading && !error} onNavigate={onNavigate} onPreview={() => setExample(true)}/>}
        {selectedExample && example && <div className="example-meeting-detail"><div className="section-heading"><h4>{exampleMeetings.find(meeting => meeting.id === selectedExample)?.title}</h4><button className="icon-action" aria-label="Close example meeting" onClick={() => setSelectedExample(null)}><Icon name="close"/></button></div><span className="sample-label">Example meeting notes</span><p><Icon name="tasks"/><strong>Next step:</strong> Share the onboarding prototype with the team.</p><p><Icon name="decisions"/><strong>Decision:</strong> Test the new flow before the next release.</p><details><summary>View example source quote</summary><blockquote>“I’ll share the prototype on Monday so we can test it before the release.”</blockquote></details></div>}
        <div className="ledger-footer"><span><span className="quiet-dot"/>{example ? "Illustrative records, for preview only" : "Extension captures & uploads, together"}</span><button className="text-button" onClick={() => onNavigate("upload")}><Icon name="upload"/>Import a transcript</button></div>
      </section>
      <aside className="dashboard-right-rail"><Attention counts={counts} onNavigate={onNavigate}/><section className="next-steps-section"><div className="section-heading"><h3>Next up</h3><Icon name="tasks"/></div>{tasks?.length ? <ul>{tasks.map(task => <li key={task.id}><button onClick={() => onNavigate("tasks")}><span className={`task-priority priority-${task.priority}`} aria-label={`${task.priority} priority`}/><span><strong>{task.title}</strong><small>{task.owner ?? "Unassigned"}{task.due_date ? ` · ${new Date(task.due_date).toLocaleDateString(undefined, { month: "short", day: "numeric" })}` : " · No due date"}</small></span><Icon name="chevron"/></button></li>)}</ul> : <div className="next-steps-empty"><span className="empty-check"><Icon name="check"/></span><p>{counts?.open_tasks ? "Review your open tasks and decide what comes next." : "Your next steps will find a home here."}</p></div>}<button className="text-button" onClick={() => onNavigate("tasks")}>Open tasks<Icon name="arrow"/></button></section></aside>
    </div>
    <section className="connections-strip"><div className="connector-monograms" aria-hidden="true"><span>N</span><span><Icon name="tasks"/></span><span><Icon name="meetings"/></span></div><div><h3>Your work doesn’t stop at notes.</h3><p>Connect Notion, Linear, Slack, and the tools your team calls home.</p></div><button className="ghost" onClick={() => onNavigate("settings")}>Connect your tools<Icon name="arrow"/></button></section>
    <p className="update-caption">{example ? "Example data is separate from your workspace." : "Workspace records refresh every 10 seconds. Audio capture stays in the extension."}</p>
  </div>;
}

function Metric({ label, value, icon, onClick }: { label: string; value?: number; icon: "meetings" | "tasks" | "decisions"; onClick: () => void }) {
  return <button className={`overview-total metric-${icon}`} onClick={onClick}><span className="metric-icon"><Icon name={icon}/></span><span className="metric-copy"><span>{label}</span><strong>{value === undefined ? "—" : value.toLocaleString()}</strong></span><Icon name="chevron"/></button>;
}

function Attention({ counts, onNavigate }: { counts?: DashboardCounts; onNavigate: Navigate }) {
  const rows = [{ label: "Overdue tasks", value: counts?.overdue_tasks, icon: "clock" as const }, { label: "Stalled tasks", value: counts?.stalled_tasks, icon: "tasks" as const }, { label: "Open blockers", value: counts?.open_blockers, icon: "alert" as const }, { label: "Open risks", value: counts?.open_risks, icon: "intelligence" as const }];
  return <section className="attention-section"><div className="section-heading"><h3>A little attention</h3><span className="attention-mark"><Icon name="intelligence"/></span></div><p>Small follow-ups. Fewer loose ends.</p><ul>{rows.map(row => <li key={row.label}><button onClick={() => onNavigate("intelligence")}><Icon name={row.icon}/><span>{row.label}</span><strong className={row.value ? "has-attention" : ""}>{row.value ?? "—"}</strong></button></li>)}</ul><button className="text-button" onClick={() => onNavigate("intelligence")}>Open intelligence<Icon name="arrow"/></button></section>;
}

function MeetingEmpty({ loading, onNavigate, onPreview }: { loading: boolean; onNavigate: Navigate; onPreview: () => void }) {
  return <div className={`meeting-first-run${loading ? " is-loading" : ""}`} aria-busy={loading}>
    <div className="meeting-flow" aria-hidden="true">
      <span><Icon name="mic"/><small>Conversation</small></span><Icon name="arrow"/>
      <span><Icon name="summary"/><small>Clear notes</small></span><Icon name="arrow"/>
      <span><Icon name="tasks"/><small>Next steps</small></span>
    </div>
    <h4>{loading ? "Getting your workspace ready…" : "Good conversations start here."}</h4>
    <p>{loading ? "Your meetings and next steps are on their way." : "Capture a conversation or upload a transcript. We’ll help turn the words into what’s next."}</p>
    <div><button className="primary" onClick={() => onNavigate("extension")}><Icon name="mic"/>Set up recording</button><button className="text-button" onClick={onPreview}>Explore an example<Icon name="arrow"/></button></div>
  </div>;
}
