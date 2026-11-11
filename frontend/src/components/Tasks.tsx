import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { api, type Task, type TaskInput } from "../api";
import { usePollingData } from "../usePollingData";
import { Icon } from "./Icon";
import "./tasks.css";
import type { Navigate } from "../navigation";

const STATUSES = ["open", "in_progress", "blocked", "done", "cancelled"];
const SOURCES = [{ id: "", label: "All tasks" }, { id: "local", label: "Created here" }, { id: "meeting", label: "Meetings" }, { id: "jira", label: "Jira" }, { id: "linear", label: "Linear" }];
const sourceNames = { local: "AOI", meeting: "Meeting", jira: "Jira", linear: "Linear" };
const PAGE_SIZE = 50;
const label = (value: string) => value.replaceAll("_", " ").replace(/^./, char => char.toUpperCase());
function errorMessage(error: unknown) {
  if (error instanceof Error) {
    try { const detail = JSON.parse(error.message.slice(error.message.indexOf("{"))).detail; if (typeof detail === "string") return detail; } catch { /* Display a safe recovery message. */ }
  }
  return "Couldn’t save this change. Check your connection and try again.";
}
function externalUrl(task: Task) {
  const url = task.jira_issue_url || task.linear_issue_url;
  try { return url && new URL(url).protocol === "https:" ? url : null; } catch { return null; }
}

export function Tasks({ onNavigate }: { onNavigate: Navigate }) {
  const [status, setStatus] = useState("");
  const [source, setSource] = useState("");
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [overdue, setOverdue] = useState(false);
  const [offset, setOffset] = useState(0);
  const [editor, setEditor] = useState<Task | "new" | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [actionError, setActionError] = useState("");
  const createRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { const timer = setTimeout(() => { setQuery(search.trim()); setOffset(0); }, 250); return () => clearTimeout(timer); }, [search]);
  const load = useCallback(async (signal: AbortSignal) => {
    const [tasks, sync] = await Promise.all([
      api.tasks({ status, source, q: query, overdue, limit: PAGE_SIZE, offset }, signal),
      api.taskSyncStatus(signal).catch(() => null),
    ]);
    return { tasks, sync };
  }, [status, source, query, overdue, offset]);
  const { data, error, loading, refresh } = usePollingData(load);
  const connected = data?.sync?.providers.filter(provider => provider.connected) ?? [];
  const connectorErrors = connected.filter(provider => provider.error);
  const hasFilters = !!(status || source || query || overdue);

  function closeEditor() { setEditor(null); createRef.current?.focus(); }
  async function saveTask(input: TaskInput) {
    if (editor && editor !== "new") {
      await api.updateTask(editor.id, input);
      setFeedback("Task updated.");
    } else {
      await api.createTask(input);
      setSource(""); setStatus(""); setSearch(""); setQuery(""); setOverdue(false); setOffset(0);
      setFeedback("Task created. You can track it here without connecting another tool.");
    }
    closeEditor(); refresh();
  }
  async function changeStatus(task: Task, next: string) {
    setPending(task.id); setActionError(""); setFeedback("");
    try {
      const result = await api.updateTask(task.id, { status: next });
      setFeedback(result.sync_pending ? `Status saved. The connector will update ${task.jira_issue_url ? "Jira" : "Linear"}.` : "Task status updated.");
      refresh();
    } catch (err) { setActionError(errorMessage(err)); }
    finally { setPending(null); }
  }
  async function syncNow() {
    setSyncing(true); setActionError(""); setFeedback("");
    try {
      const result = await api.syncTasks();
      const failed = result.results.filter(provider => provider.error);
      if (failed.length) setActionError(`Couldn’t import from ${failed.map(provider => label(provider.provider)).join(" and ")}. Check the connector’s read access and retry.`);
      const created = result.results.reduce((sum, provider) => sum + provider.created, 0);
      const updated = result.results.reduce((sum, provider) => sum + provider.updated, 0);
      if (!failed.length) setFeedback(result.results.some(provider => provider.busy) ? "An import is already running. Tasks will refresh automatically." : `Sync checked: ${created} imported, ${updated} updated.${result.providers.some(provider => provider.has_more) ? " More tasks will import automatically." : ""}`);
      refresh();
    } catch (err) { setActionError(errorMessage(err)); }
    finally { setSyncing(false); }
  }
  function clearFilters() { setSource(""); setStatus(""); setSearch(""); setQuery(""); setOverdue(false); setOffset(0); }

  return <div className="workspace-page task-workspace">
    <header className="page-heading task-page-heading"><div><h2>Keep work moving.</h2><p className="muted">Your team’s tasks. From conversations, connected tools, or a fresh start here.</p></div><button className="primary" ref={createRef} onClick={() => { setActionError(""); setFeedback(""); setEditor("new"); }}><Icon name="plus"/>New task</button></header>
    <div className="task-connection-bar"><div><span className="task-presence"/><span>{connected.length ? `Connected to ${connected.map(provider => label(provider.provider)).join(" & ")}` : data?.sync ? "Your workspace. Your way." : "Tasks & connected tools"}</span><small>{connected.length ? `Imports checked every ${data?.sync?.interval_seconds}s` : "Create and track tasks here. Connectors are optional."}</small></div><div className="task-connection-actions">{connected.length ? <button className="ghost" disabled={syncing} onClick={() => void syncNow()}><Icon name="refresh" className={syncing ? "is-spinning" : ""}/>{syncing ? "Syncing…" : "Sync now"}</button> : <button onClick={() => onNavigate("settings")} className="task-connect-link">Connect your tools<Icon name="arrow"/></button>}<button className="icon-action" onClick={refresh} disabled={loading} aria-label="Refresh tasks"><Icon name="refresh"/></button></div></div>
    {connected.some(provider => provider.has_more) && <p className="task-sync-note" role="status">Importing your existing tasks in batches. More will appear as the import continues.</p>}
    {connected.length > 0 && !data?.sync?.status_sync_enabled && <p className="task-sync-note">This connection imports tasks for review. Update linked statuses in their original tool.</p>}
    {connectorErrors.map(provider => <p className="load-error" role="alert" key={provider.provider}>{label(provider.provider)}: {provider.error}</p>)}
    {feedback && <div className="task-feedback" role="status"><Icon name="check"/>{feedback}<button className="icon-action" aria-label="Dismiss message" onClick={() => setFeedback("")}><Icon name="close"/></button></div>}
    {actionError && <p className="load-error" role="alert">{actionError}</p>}
    {editor && <TaskEditor key={typeof editor === "string" ? editor : editor.id} task={editor === "new" ? undefined : editor} onSave={saveTask} onCancel={closeEditor}/>}
    <section className="task-ledger" aria-label="Workspace tasks">
      <div className="task-source-tabs" role="group" aria-label="Task source">{SOURCES.map(item => <button key={item.id} aria-pressed={source === item.id} onClick={() => { setSource(item.id); setOffset(0); }}>{item.label}</button>)}</div>
      <div className="task-filter-bar"><label className="task-search"><Icon name="search"/><input aria-label="Search task titles or owners" placeholder="Search tasks or owners…" value={search} maxLength={200} onChange={event => setSearch(event.target.value)}/>{search && <button aria-label="Clear task search" className="icon-action" onClick={() => setSearch("")}><Icon name="close"/></button>}</label><div className="task-filter-controls"><select aria-label="Task status" value={status} onChange={event => { setStatus(event.target.value); setOffset(0); }}><option value="">All statuses</option>{STATUSES.map(value => <option value={value} key={value}>{label(value)}</option>)}</select><label className="task-overdue"><input type="checkbox" checked={overdue} onChange={event => { setOverdue(event.target.checked); setOffset(0); }}/><Icon name="clock"/>Overdue</label></div></div>
      {error ? <div className="task-load-error" role="alert"><Icon name="link"/><h3>Tasks couldn’t be loaded.</h3><p>Check your backend connection, then try again. Your tasks haven’t changed.</p><button className="ghost" disabled={loading} onClick={refresh}>Try again<Icon name="refresh"/></button></div> : !data ? <div className="task-skeleton" role="status" aria-label="Loading tasks">{[0, 1, 2].map(index => <div key={index}><span/><i/><i/></div>)}</div> : data.tasks.items.length ? <div className="task-table-scroll"><table className="task-table" role="table"><thead role="rowgroup"><tr role="row"><th scope="col" role="columnheader">Task</th><th scope="col" role="columnheader">Source</th><th scope="col" role="columnheader">Owner</th><th scope="col" role="columnheader">Priority</th><th scope="col" role="columnheader">Due date</th><th scope="col" role="columnheader">Status</th><th scope="col" role="columnheader"><span className="sr-only">Actions</span></th></tr></thead><tbody role="rowgroup">{data.tasks.items.map(task => {
        const link = externalUrl(task);
        const remote = !!link || task.source === "jira" || task.source === "linear";
        const isOverdue = !!task.due_date && new Date(task.due_date) < new Date() && !["done", "cancelled"].includes(task.status);
        return <tr role="row" key={task.id} className={task.status === "done" ? "task-is-done" : ""}><td role="cell" className="task-title-cell"><strong>{task.title}</strong>{task.description && <p>{task.description}</p>}{task.source_quote && <details className="task-evidence"><summary>Meeting evidence</summary><blockquote>{task.source_quote}</blockquote></details>}{task.sync_pending && <small className="task-pending"><Icon name="refresh"/>Status waiting to sync</small>}{task.sync_error && <small className="task-row-error" role="status">{task.sync_error}</small>}</td><td role="cell" data-label="Source"><span className={`task-source-badge source-${task.source}`}><Icon name={task.source === "meeting" ? "mic" : task.source === "local" ? "tasks" : "link"}/>{sourceNames[task.source] ?? "Meeting"}</span>{link && <a className="task-external-link" href={link} target="_blank" rel="noopener noreferrer">Open {task.jira_issue_url ? "Jira" : "Linear"}<Icon name="arrow"/></a>}</td><td role="cell" data-label="Owner"><span className="task-owner">{task.owner && <span aria-hidden="true">{task.owner.charAt(0).toUpperCase()}</span>}{task.owner || "Unassigned"}</span></td><td role="cell" data-label="Priority"><span className={`task-priority-label task-priority-${task.priority}`}><i/>{label(task.priority)}</span></td><td role="cell" data-label="Due date"><time className={isOverdue ? "task-due-overdue" : ""} dateTime={task.due_date ?? undefined}>{task.due_date ? new Date(task.due_date).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" }) : "No due date"}</time></td><td role="cell" data-label="Status"><select aria-label={`Status for ${task.title}`} className={`task-status-select task-status-${task.status}`} value={task.status} disabled={pending === task.id || (!!link && !data.sync?.status_sync_enabled)} onChange={event => void changeStatus(task, event.target.value)}>{STATUSES.map(value => <option value={value} key={value}>{label(value)}</option>)}</select></td><td role="cell" className="task-row-actions">{!remote && <button className="task-edit-button" onClick={() => { setEditor(task); setActionError(""); }} aria-label={`Edit ${task.title}`}>Edit</button>}</td></tr>;
      })}</tbody></table></div> : <div className="task-empty"><span className="task-empty-symbol"><Icon name={hasFilters ? "search" : "tasks"}/></span><h3>{hasFilters ? "No tasks match this view." : "Make room for what’s next."}</h3><p>{hasFilters ? "Try another search or clear your filters to see the rest of your team’s work." : "Add your first task here, capture one from a meeting, or connect Jira or Linear to bring existing work into view."}</p>{hasFilters ? <button className="ghost" onClick={clearFilters}>Clear filters<Icon name="arrow"/></button> : <button className="primary" onClick={() => setEditor("new")}><Icon name="plus"/>Create your first task</button>}</div>}
      {data && <div className="task-ledger-footer"><span>{data.tasks.total ? `${offset + 1}–${Math.min(offset + PAGE_SIZE, data.tasks.total)} of ${data.tasks.total} tasks` : "0 tasks"}{loading && " · Refreshing…"}</span><div><button className="ghost" disabled={offset === 0 || loading} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous</button><button className="ghost" disabled={offset + PAGE_SIZE >= data.tasks.total || loading} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next</button></div></div>}
    </section>
    <p className="task-bottom-note"><Icon name="book"/>One shared place to follow through. Tasks created here stay in AOI; connected issues link back to their original tool.</p>
  </div>;
}

function TaskEditor({ task, onSave, onCancel }: { task?: Task; onSave: (input: TaskInput) => Promise<void>; onCancel: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const titleRef = useRef<HTMLInputElement>(null);
  useEffect(() => { titleRef.current?.focus(); titleRef.current?.scrollIntoView({ block: "nearest", behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" }); }, []);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError("");
    const due = String(form.get("due_date") || "");
    try {
      await onSave({ title: String(form.get("title")).trim(), description: String(form.get("description")).trim() || null, owner: String(form.get("owner")).trim() || null, priority: String(form.get("priority")), due_date: due ? new Date(`${due}T12:00:00`).toISOString() : null });
    } catch (err) { setError(errorMessage(err)); setBusy(false); }
  }
  return <form className="task-editor" onSubmit={submit} aria-label={task ? "Edit task" : "Create task"}><div className="section-heading"><h3>{task ? "Edit task" : "A new next step."}</h3><button type="button" className="icon-action" aria-label="Close task editor" disabled={busy} onClick={onCancel}><Icon name="close"/></button></div><fieldset disabled={busy}><label htmlFor="task-title">What needs to happen?<input ref={titleRef} id="task-title" name="title" placeholder="Give your team a clear next step" defaultValue={task?.title} required maxLength={512} pattern=".*\S.*"/></label><label htmlFor="task-description">Details <span>(optional)</span><textarea id="task-description" name="description" placeholder="Add context, a checklist, or useful links…" defaultValue={task?.description ?? ""} maxLength={100000}/></label><div className="task-editor-fields"><label htmlFor="task-owner">Owner<input id="task-owner" name="owner" placeholder="Who’s taking this on?" defaultValue={task?.owner ?? ""} maxLength={128}/></label><label htmlFor="task-due">Due date<input type="date" id="task-due" name="due_date" defaultValue={task?.due_date ? new Date(task.due_date).toLocaleDateString("en-CA") : ""}/></label><label htmlFor="task-priority">Priority<select id="task-priority" name="priority" aria-label="Priority" defaultValue={task?.priority ?? "medium"}>{["low", "medium", "high", "urgent"].map(value => <option key={value} value={value}>{label(value)}</option>)}</select></label></div></fieldset>{error && <p className="load-error" role="alert">{error}</p>}<div className="task-editor-footer"><span><Icon name="tasks"/>{task ? "Changes save to this workspace." : "Track it here. No connector or AI key needed."}</span><div><button type="button" className="ghost" disabled={busy} onClick={onCancel}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Saving…" : task ? "Save changes" : "Create task"}<Icon name="arrow"/></button></div></div></form>;
}
