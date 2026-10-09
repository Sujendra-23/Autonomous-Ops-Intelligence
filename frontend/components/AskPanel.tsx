"use client";

import { useChat } from "@ai-sdk/react";
import { DefaultChatTransport } from "ai";
import { useMemo, useState } from "react";
import { authHeaders } from "@/lib/api";

type AnalyticsOutput =
  | { error: string }
  | { columns: string[]; rows: unknown[][]; truncated: boolean; owners_masked: boolean };
type SearchOutput = { error: string } | { hits: { title: string; score: number; content: string }[] };

function AnalyticsTable({ output }: { output: AnalyticsOutput }) {
  if ("error" in output) return <div role="alert">{output.error}</div>;
  return (
    <div>
      <div className="ask-table-wrap">
        <table>
          <thead>
            <tr>{output.columns.map((c) => <th key={c}>{c}</th>)}</tr>
          </thead>
          <tbody>
            {output.rows.map((row, i) => (
              <tr key={i}>{row.map((cell, j) => <td key={j}>{cell === null ? "" : String(cell)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      </div>
      {output.rows.length === 0 && <p className="muted">No rows.</p>}
      {output.truncated && <p className="muted">Showing the first rows only.</p>}
      {output.owners_masked && <p className="muted">Owner names are masked.</p>}
    </div>
  );
}

export function AskPanel() {
  const [input, setInput] = useState("");
  // Credentials are read per request so a key typed after the page loads is still sent.
  const transport = useMemo(
    () => new DefaultChatTransport({ api: "/api/console/chat", headers: () => authHeaders() }),
    [],
  );
  const { messages, sendMessage, status, error, stop } = useChat({ transport });
  const busy = status === "submitted" || status === "streaming";

  const submit = () => {
    const text = input.trim();
    if (!text || busy) return;
    sendMessage({ text });
    setInput("");
  };

  return (
    <div className="panel">
      <h3>Ask</h3>
      <p className="muted">
        Ask in plain English. Counts and lists run as a validated read-only SQL query and appear as a table.
        Questions about what was said search the transcripts. Answers stream in as they are written.
      </p>
      <div style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="How many tasks are overdue by priority?"
          aria-label="Question"
          maxLength={2000}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        {busy ? (
          <button className="ghost" onClick={() => stop()}>Stop</button>
        ) : (
          <button className="primary" disabled={!input.trim()} onClick={submit}>Ask</button>
        )}
      </div>

      <div className="ask-log" aria-live="polite">
        {messages.map((m) => (
          <div key={m.id} className={`ask-msg ${m.role}`}>
            <span className="role">{m.role === "user" ? "You" : "Agent"}</span>
            {m.parts.map((part, i) => {
              if (part.type === "text") return <span key={i}>{part.text}</span>;
              if (part.type === "tool-askAnalytics") {
                if (part.state === "output-available") return <AnalyticsTable key={i} output={part.output as AnalyticsOutput} />;
                if (part.state === "output-error") return <div key={i} role="alert">The query failed.</div>;
                return <div key={i} className="muted">Running a query…</div>;
              }
              if (part.type === "tool-searchTranscripts") {
                if (part.state === "output-available") {
                  const out = part.output as SearchOutput;
                  if ("error" in out) return <div key={i} role="alert">{out.error}</div>;
                  return <div key={i} className="muted">Searched {out.hits.length} transcript passages.</div>;
                }
                if (part.state === "output-error") return <div key={i} role="alert">The search failed.</div>;
                return <div key={i} className="muted">Searching transcripts…</div>;
              }
              return null;
            })}
          </div>
        ))}
      </div>
      {error && <div role="alert" style={{ marginTop: 12 }}>
        {error.message.includes("Not signed in") ? "You are not signed in to the backend." : "The assistant could not answer. Check that the Anthropic key is set on the console server."}
      </div>}
    </div>
  );
}
