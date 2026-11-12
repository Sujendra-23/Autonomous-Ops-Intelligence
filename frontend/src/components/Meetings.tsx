import { lazy, Suspense, useCallback, useState } from "react";
import { api, type TranscriptSummary } from "../api";
import type { Navigate, MeetingView } from "../navigation";
import { usePollingData } from "../usePollingData";
import { MeetingExports } from "./MeetingExports";
import "./meeting-library.css";
import { MeetingHub } from "./MeetingHub";
const CallRoom = lazy(() => import("./CallRoom"));

const PAGE_SIZE = 20;
const statusNames: Record<string, string> = { live: "Live", completed: "Completed", failed: "Failed", pending: "Queued", processing: "Processing", queued: "Queued" };
export function MeetingStatus({ status }: { status: string }) {
  return <span className={`meeting-status status-${Object.hasOwn(statusNames, status) ? status : "unknown"}`}>{status === "live" && <span className="live-dot" aria-hidden="true" />}{statusNames[status] ?? status}</span>;
}

export function MeetingList({ meetings, onOpen }: { meetings: TranscriptSummary[]; onOpen: (id: string) => void }) {
  return <ul className="meeting-list">{meetings.map(meeting => <li key={meeting.id}><button className="meeting-row" onClick={() => onOpen(meeting.id)}><span className="meeting-source" aria-hidden="true"><svg viewBox="0 0 24 24">{(meeting.source === "live" || meeting.source === "desktop") ? <><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/></> : <><path d="M14 3H5v18h14V8zm0 0v5h5M8 12h8m-8 4h6"/></>}</svg></span><span className="meeting-copy"><strong>{meeting.title}</strong><span>{new Date(meeting.meeting_date ?? meeting.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}<span aria-hidden="true"> · </span>{meeting.source === "desktop" ? "Desktop recorder" : meeting.source === "live" ? "Browser extension" : "Uploaded transcript"}</span></span><MeetingStatus status={meeting.status} /><span className="row-arrow" aria-hidden="true">→</span></button></li>)}</ul>;
}

export function Meetings({ meetingId, roomId, meetingView, onNavigate }: { meetingId?: string; roomId?:string; meetingView?:MeetingView; onNavigate: Navigate }) {
  if(roomId) return <Suspense fallback={<p role="status">Opening meeting…</p>}><CallRoom key={roomId} id={roomId} onNavigate={onNavigate}/></Suspense>;
  return meetingId ? <MeetingDetail key={meetingId} id={meetingId} onNavigate={onNavigate} /> : <MeetingHistory meetingView={meetingView} onNavigate={onNavigate} />;
}

function MeetingHistory({ onNavigate, meetingView }: { onNavigate: Navigate; meetingView?:MeetingView }) {
  const [offset, setOffset] = useState(0);
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState({ q: "", status: "", days: "" });
  const load = useCallback((signal: AbortSignal) => api.transcripts({ limit: PAGE_SIZE, offset, q: filters.q, status: filters.status, days: filters.days ? Number(filters.days) : undefined }, signal), [offset, filters]);
  const { data, error, loading, refresh } = usePollingData(load);
  const filtered = Boolean(filters.q || filters.status || filters.days);
  function clearFilters() { setQuery(""); setFilters({ q: "", status: "", days: "" }); setOffset(0); }
  return <MeetingHub onNavigate={onNavigate} meetingView={meetingView}>
    <form className="meeting-library-filters" aria-label="Find meeting notes" onSubmit={event => {
      event.preventDefault(); const form = new FormData(event.currentTarget);
      setFilters({ q: query.trim(), status: String(form.get("status") || ""), days: String(form.get("days") || "") }); setOffset(0);
    }}>
      <label htmlFor="meeting-search">Search notes<input id="meeting-search" type="search" maxLength={200} placeholder="Title or transcript text" value={query} onChange={event => setQuery(event.target.value)} /></label>
      <label htmlFor="meeting-filter-status">Status<select aria-label="Status" id="meeting-filter-status" name="status" defaultValue=""><option value="">Any status</option><option value="completed">Completed</option><option value="live">Live</option><option value="failed">Failed</option></select></label>
      <label htmlFor="meeting-filter-period">When<select aria-label="When" id="meeting-filter-period" name="days" defaultValue=""><option value="">All time</option><option value="7">Last 7 days</option><option value="30">Last 30 days</option><option value="90">Last 90 days</option></select></label>
      <button className="ghost">Search notes</button>
      {filtered && <button type="button" className="text-button" onClick={event => { event.currentTarget.form?.reset(); clearFilters(); }}>Clear filters</button>}
    </form>
    {error && <p className="load-error" role="alert">Couldn’t refresh meeting notes. Check your connection and try Refresh notes.</p>}
    {!data && !error && <p className="panel muted" role="status">Loading meeting notes…</p>}
    {data && <section className="meeting-section"><div className="section-heading"><h3>Meeting notes <span className="muted">({data.total})</span></h3><button className="text-button" onClick={refresh} disabled={loading}>{loading ? "Updating…" : "Refresh notes"}</button></div>{data.items.length ? <MeetingList meetings={data.items} onOpen={id => onNavigate("meetings", id)} /> : <div className="meeting-empty"><h4>{filtered ? "No notes match these filters." : "No meeting notes yet."}</h4><p>{filtered ? "Try different words or a wider time period." : "Record a meeting or upload a transcript to find its notes here."}</p>{!filtered && <button className="ghost" onClick={() => onNavigate("extension")}>Set up recording</button>}</div>}<div className="meeting-pagination"><span className="muted">{data.total ? `${offset + 1}–${Math.min(offset + data.items.length, data.total)} of ${data.total}` : "0 meeting notes"}</span><div><button className="ghost" disabled={offset === 0 || loading} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous</button><button className="ghost" disabled={offset + PAGE_SIZE >= data.total || loading} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next</button></div></div></section>}
    <p className="update-caption">New captures appear automatically. Updates every 10 seconds.</p>
  </MeetingHub>;
}

function MeetingDetail({ id, onNavigate }: { id: string; onNavigate: Navigate }) {
  const load = useCallback((signal: AbortSignal) => api.transcript(id, signal), [id]);
  const { data, error, loading, refresh } = usePollingData(load);
  const [tab, setTab] = useState<"notes" | "transcript">("notes");
  const [copyStatus, setCopyStatus] = useState("");
  async function copyTranscript() {
    try { await navigator.clipboard.writeText(data?.content ?? ""); setCopyStatus("Transcript copied."); }
    catch { setCopyStatus("Couldn’t copy. Select the transcript text and copy it manually."); }
  }
  return <div className="workspace-page meeting-detail"><button className="text-button back-link" onClick={() => onNavigate("meetings", undefined, "captured")}><span aria-hidden="true">←</span> Meeting notes</button>
    {error && <div className="load-error" role="alert"><p>Couldn’t load this meeting. Check the connection and make sure you have access to this workspace.</p><button className="ghost" onClick={refresh} disabled={loading}>Try again</button></div>}
    {!data && !error && <p className="panel muted" role="status">Loading meeting…</p>}
    {data && <><header className="page-heading"><div><h2>{data.title}</h2><p className="muted">{new Date(data.meeting_date ?? data.created_at).toLocaleString()} · {data.source === "desktop" ? "Captured with AOI Desktop" : data.source === "live" ? "Captured with the extension" : "Uploaded transcript"}</p></div><MeetingStatus status={data.status} /></header>
      {(data.source === "live" || data.source === "desktop") && data.transcription_language && <p className="muted">Listening language: {data.transcription_language === "auto" ? "Auto-detect / mixed languages" : new Intl.DisplayNames(["en"], { type: "language" }).of(data.transcription_language)}</p>}
      {data.participants?.length ? <p className="muted">Participants: {data.participants.join(", ")}</p> : null}
      {data.status === "live" && <p className="live-notice" role="status"><span className="live-dot" aria-hidden="true" />Capture is running in {data.source === "desktop" ? "AOI Desktop" : "the extension"}. Notes and transcript update here every 10 seconds.</p>}
      {data.status === "failed" && <p className="load-error" role="alert">Processing failed. Your transcript is saved. Ask your workspace administrator to check processing.</p>}
      <MeetingExports key={data.id} meeting={data} />
      <div className="meeting-tabs" role="group" aria-label="Meeting content"><button aria-pressed={tab === "notes"} onClick={() => setTab("notes")}>Notes <span>{data.tasks.length + data.decisions.length + data.risks.length + data.blockers.length}</span></button><button aria-pressed={tab === "transcript"} onClick={() => setTab("transcript")}>Transcript</button></div>
      {tab === "transcript" ? <section className="meeting-transcript"><div className="section-heading"><h3>Transcript</h3><button className="ghost" onClick={copyTranscript} disabled={!data.content}>Copy transcript</button></div><p className="muted" role="status">{copyStatus}</p>{data.segments?.some(segment => segment.needs_review) && <details className="transcript-review"><summary>Review uncertain passages ({data.segments.filter(segment => segment.needs_review).length})</summary><p>These passages have low provider confidence. This is a review cue, not a guarantee that other words are correct. Check names, dates and commitments against the audio.</p>{data.segments.filter(segment => segment.needs_review).map((segment,index) => <blockquote key={index} dir="auto">{segment.text}</blockquote>)}</details>}<div className="transcript-content" dir="auto">{data.content || "No transcript text yet. Text will appear as speech is transcribed."}</div></section> : <div className="meeting-notes">
        {!data.tasks.length && !data.decisions.length && !data.risks.length && !data.blockers.length && <p className="meeting-empty">No structured notes yet.{data.status === "live" ? " Keep the recorder running while you talk." : " Check the transcript or return after processing completes."}</p>}
        {!!data.tasks.length && <section><div className="section-heading"><h3>Tasks <span className="muted">({data.tasks.length})</span></h3><button className="text-button" onClick={() => onNavigate("tasks")}>Manage tasks →</button></div>{data.tasks.map(task => <article className="meeting-note" key={task.id}><h4>{task.title}</h4><p className="muted">{task.owner ?? "Unassigned"} · {task.status.replaceAll("_", " ")} · {task.priority}{task.due_date ? ` · Due ${new Date(task.due_date).toLocaleDateString()}` : ""}{task.confidence != null ? ` · ${Math.round(task.confidence * 100)}% confidence` : ""}</p><Evidence quote={task.source_quote} /></article>)}</section>}
        {!!data.decisions.length && <section><h3>Decisions <span className="muted">({data.decisions.length})</span></h3>{data.decisions.map(decision => <article className="meeting-note" key={decision.id}><h4>{decision.summary}</h4>{decision.rationale && <p className="muted">{decision.rationale}</p>}{!!decision.decided_by?.length && <p className="muted">Decided by {decision.decided_by.join(", ")}</p>}<Evidence quote={decision.source_quote} /></article>)}</section>}
        {!!data.risks.length && <section><h3>Risks <span className="muted">({data.risks.length})</span></h3>{data.risks.map(risk => <article className="meeting-note" key={risk.id}><h4>{risk.title}</h4><p className="muted">{risk.severity} severity · {risk.likelihood} likelihood</p>{risk.mitigation && <p>{risk.mitigation}</p>}<Evidence quote={risk.source_quote} /></article>)}</section>}
        {!!data.blockers.length && <section><h3>Blockers <span className="muted">({data.blockers.length})</span></h3>{data.blockers.map(blocker => <article className="meeting-note" key={blocker.id}><h4>{blocker.summary}</h4><p className="muted">{blocker.severity} · {blocker.status}{blocker.blocked_party ? ` · Blocking ${blocker.blocked_party}` : ""}{blocker.needs_from ? ` · Needs help from ${blocker.needs_from}` : ""}</p></article>)}</section>}
      </div>}
    </>}
  </div>;
}

function Evidence({ quote }: { quote: string | null }) {
  return quote ? <details className="meeting-evidence"><summary>View source quote</summary><blockquote dir="auto">{quote}</blockquote></details> : null;
}
