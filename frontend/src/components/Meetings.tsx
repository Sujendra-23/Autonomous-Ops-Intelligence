import { useCallback, useState } from "react";
import { api, type TranscriptSummary } from "../api";
import type { Navigate } from "../navigation";
import { usePollingData } from "../usePollingData";

const PAGE_SIZE = 20;
const statusNames: Record<string, string> = { live: "Live", completed: "Completed", failed: "Failed", pending: "Queued", processing: "Processing", queued: "Queued" };
export function MeetingStatus({ status }: { status: string }) {
  return <span className={`meeting-status status-${Object.hasOwn(statusNames, status) ? status : "unknown"}`}>{status === "live" && <span className="live-dot" aria-hidden="true" />}{statusNames[status] ?? status}</span>;
}

export function MeetingList({ meetings, onOpen }: { meetings: TranscriptSummary[]; onOpen: (id: string) => void }) {
  return <ul className="meeting-list">{meetings.map(meeting => <li key={meeting.id}><button className="meeting-row" onClick={() => onOpen(meeting.id)}><span className="meeting-source" aria-hidden="true"><svg viewBox="0 0 24 24">{(meeting.source === "live" || meeting.source === "desktop") ? <><rect x="9" y="3" width="6" height="12" rx="3"/><path d="M5 10v2a7 7 0 0 0 14 0v-2m-7 9v3m-4 0h8"/></> : <><path d="M14 3H5v18h14V8zm0 0v5h5M8 12h8m-8 4h6"/></>}</svg></span><span className="meeting-copy"><strong>{meeting.title}</strong><span>{new Date(meeting.meeting_date ?? meeting.created_at).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}<span aria-hidden="true"> · </span>{meeting.source === "desktop" ? "Desktop recorder" : meeting.source === "live" ? "Browser extension" : "Uploaded transcript"}</span></span><MeetingStatus status={meeting.status} /><span className="row-arrow" aria-hidden="true">→</span></button></li>)}</ul>;
}

export function Meetings({ meetingId, onNavigate }: { meetingId?: string; onNavigate: Navigate }) {
  return meetingId ? <MeetingDetail key={meetingId} id={meetingId} onNavigate={onNavigate} /> : <MeetingHistory onNavigate={onNavigate} />;
}

function MeetingHistory({ onNavigate }: { onNavigate: Navigate }) {
  const [offset, setOffset] = useState(0);
  const load = useCallback((signal: AbortSignal) => api.transcripts({ limit: PAGE_SIZE, offset }, signal), [offset]);
  const { data, error, loading, refresh } = usePollingData(load);
  return <div className="workspace-page"><header className="page-heading"><div><h2>Meetings</h2><p className="muted">Live captures and uploaded transcripts, together in one workspace.</p></div><div className="page-actions"><button className="ghost" onClick={refresh} disabled={loading}>{loading ? "Updating…" : "Refresh"}</button><button className="primary" onClick={() => onNavigate("upload")}>Upload transcript</button></div></header>
    {error && <p className="load-error" role="alert">Couldn’t refresh meetings. Check your backend connection and try Refresh.</p>}
    {!data && !error && <p className="panel muted" role="status">Loading meetings…</p>}
    {data && <section className="meeting-section"><div className="section-heading"><h3>Meeting history</h3><span className="muted">{data.total} total</span></div>{data.items.length ? <MeetingList meetings={data.items} onOpen={id => onNavigate("meetings", id)} /> : <div className="meeting-empty"><h4>No meetings here yet.</h4><p>Use the extension to capture your next meeting, or upload an existing transcript.</p><button className="ghost" onClick={() => onNavigate("extension")}>Set up the extension</button></div>}<div className="meeting-pagination"><span className="muted">{data.total ? `${offset + 1}–${Math.min(offset + data.items.length, data.total)} of ${data.total}` : "0 meetings"}</span><div><button className="ghost" disabled={offset === 0 || loading} onClick={() => setOffset(value => Math.max(0, value - PAGE_SIZE))}>Previous</button><button className="ghost" disabled={offset + PAGE_SIZE >= data.total || loading} onClick={() => setOffset(value => value + PAGE_SIZE)}>Next</button></div></div></section>}
    <p className="update-caption">New captures appear automatically. Updates every 10 seconds.</p>
  </div>;
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
  return <div className="workspace-page meeting-detail"><button className="text-button back-link" onClick={() => onNavigate("meetings")}><span aria-hidden="true">←</span> All meetings</button>
    {error && <div className="load-error" role="alert"><p>Couldn’t load this meeting. Check the connection and make sure you have access to this workspace.</p><button className="ghost" onClick={refresh} disabled={loading}>Try again</button></div>}
    {!data && !error && <p className="panel muted" role="status">Loading meeting…</p>}
    {data && <><header className="page-heading"><div><h2>{data.title}</h2><p className="muted">{new Date(data.meeting_date ?? data.created_at).toLocaleString()} · {data.source === "desktop" ? "Captured with AOI Desktop" : data.source === "live" ? "Captured with the extension" : "Uploaded transcript"}</p></div><MeetingStatus status={data.status} /></header>
      {(data.source === "live" || data.source === "desktop") && data.transcription_language && <p className="muted">Listening language: {data.transcription_language === "auto" ? "Auto-detect / mixed languages" : new Intl.DisplayNames(["en"], { type: "language" }).of(data.transcription_language)}</p>}
      {data.participants?.length ? <p className="muted">Participants: {data.participants.join(", ")}</p> : null}
      {data.status === "live" && <p className="live-notice" role="status"><span className="live-dot" aria-hidden="true" />Capture is running in {data.source === "desktop" ? "AOI Desktop" : "the extension"}. Notes and transcript update here every 10 seconds.</p>}
      {data.status === "failed" && <p className="load-error" role="alert">Processing failed. Your transcript is saved. Ask your workspace administrator to check processing.</p>}
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
