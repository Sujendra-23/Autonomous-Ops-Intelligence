import { useState } from "react";
import type { TranscriptDetail } from "../api";

function date(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "Date not recorded" : parsed.toLocaleString();
}
export function meetingRecap(meeting: TranscriptDetail, followUp = false): string {
  const tasks = meeting.tasks.map(task => `- ${task.title} — ${task.owner || "Unassigned"}${task.due_date ? `; due ${date(task.due_date)}` : ""}; ${task.status.replaceAll("_", " ")}`);
  const decisions = meeting.decisions.map(item => `- ${item.summary}`);
  const risks = meeting.risks.map(item => `- ${item.title}${item.mitigation ? ` — ${item.mitigation}` : ""}`);
  const blockers = meeting.blockers.map(item => `- ${item.summary}${item.needs_from ? ` — needs ${item.needs_from}` : ""}`);
  const sections = [["Decisions", decisions], ["Action items", tasks], ["Risks", risks], ["Blockers", blockers]] as const;
  const output = followUp
    ? [`Subject: Follow-up: ${meeting.title}`, "", "Hi team,", "", `Here are the saved outcomes from ${meeting.title}.`]
    : [meeting.title, `Meeting: ${date(meeting.meeting_date || meeting.created_at)}`, `Classification: ${meeting.sensitivity_label || "internal"}`, ...(meeting.participants?.length ? [`Participants: ${meeting.participants.join(", ")}`] : [])];
  if (meeting.status !== "completed") output.push("", "Working notes: processing is not complete. Review before sharing.");
  let outcomes = false;
  for (const [heading, items] of sections) if (items.length) {
    outcomes = true;
    output.push("", heading, ...items);
  }
  if (!outcomes) output.push("", "No structured outcomes captured yet. Review the transcript before sharing.");
  if (followUp) output.push("", "Please confirm your next steps and flag any corrections.");
  output.push("", "Prepared from saved AOI records. Review details and recipients before sharing.");
  return output.join("\n");
}
function download(meeting: TranscriptDetail, suffix: string, content: string) {
  const file = (meeting.title.replace(/[^\p{L}\p{N}_-]+/gu, "-").slice(0, 70) || "meeting") + suffix;
  const url = URL.createObjectURL(new Blob([content], { type: "text/plain;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url; link.download = file; link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function MeetingExports({ meeting }: { meeting: TranscriptDetail }) {
  const [format, setFormat] = useState<"recap" | "follow-up">("recap");
  const [drafts, setDrafts] = useState(() => ({ recap: meetingRecap(meeting), "follow-up": meetingRecap(meeting, true) }));
  const draft = drafts[format];
  const [message, setMessage] = useState("");
  const label = format === "follow-up" ? "Follow-up draft" : "Meeting recap";
  return <details className="meeting-exports">
    <summary>Recap & follow-up</summary>
    <div className="meeting-export-body">
      <p>Use the saved decisions and tasks to follow up. Edits stay in this tab until you leave.</p>
      <label htmlFor="meeting-output-format">Prepare<select aria-label="Prepare" id="meeting-output-format" value={format} onChange={event => {
        setFormat(event.target.value as "recap" | "follow-up"); setMessage("");
      }}><option value="recap">Meeting recap</option><option value="follow-up">Follow-up draft</option></select></label>
      <label htmlFor="meeting-output-text">{label}</label><textarea aria-label={label} id="meeting-output-text" value={draft} onChange={event => setDrafts({ ...drafts, [format]: event.target.value })} />
      <div className="meeting-export-actions">
        <button className="primary" disabled={!draft.trim()} onClick={() => void navigator.clipboard.writeText(draft).then(() => setMessage(`${label} copied.`)).catch(() => setMessage("Couldn’t copy. Select the draft text and copy it manually."))}>Copy {format === "follow-up" ? "draft" : "recap"}</button>
        <button className="ghost" disabled={!draft.trim()} onClick={() => { download(meeting, `-${format}.txt`, draft); setMessage(`${label} downloaded.`); }}>Download {format === "follow-up" ? "draft" : "recap"}</button>
        <button className="text-button" disabled={!meeting.content} onClick={() => { download(meeting, "-transcript.txt", meeting.content); setMessage("Transcript downloaded."); }}>Download transcript</button>
      </div>
      <p className="muted" role="status">{message || "Nothing is sent automatically. Check details and recipients before sharing."}</p>
    </div>
  </details>;
}
