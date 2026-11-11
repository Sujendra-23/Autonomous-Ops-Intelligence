import { useRef, useState } from "react";
import { api } from "../api";
import { Icon } from "./Icon";

type Mode = "text" | "video";
type UploadStatus = "idle" | "transcribing" | "extracting" | "done" | "error";

const ACCEPTED = ".mp4,.mp3,.m4a,.wav,.ogg,.webm,.flac";
const MAX_MB = 100; // 100 MiB

const STEPS: { key: UploadStatus; label: string; hint?: string }[] = [
  { key: "transcribing", label: "Transcribing",  hint: "may take a minute" },
  { key: "extracting",   label: "Extracting",    hint: "tasks, decisions, risks…" },
  { key: "done",         label: "Done" },
];
const STEP_ORDER: UploadStatus[] = ["transcribing", "extracting", "done"];

export function TranscriptUpload({ onIngested }: { onIngested: () => void }) {
  const [mode, setMode] = useState<Mode>("video");

  const [title, setTitle]               = useState("");
  const [projectHint, setProjectHint]   = useState("");
  const [participants, setParticipants] = useState("");
  const [content, setContent]           = useState("");

  const [file, setFile]       = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const [status, setStatus]   = useState<UploadStatus>("idle");
  const [error, setError]     = useState<string | null>(null);

  const busy = status === "transcribing" || status === "extracting";

  // ── Core upload flow ────────────────────────────────────────────────────────
  // Called immediately when a file is selected / dropped — no extra button click.
  const startUpload = async (f: File) => {
    setError(null);

    if (f.size > MAX_MB * 1024 * 1024) {
      setError(`File is ${(f.size / 1024 / 1024 / 1024).toFixed(2)} GB — limit is 100 MB.`);
      setStatus("error");
      return;
    }

    setFile(f);
    if (!title) setTitle(f.name.replace(/\.[^.]+$/, ""));
    setStatus("transcribing");

    try {
      const ingested = await api.ingestVideo({
        file: f,
        title: title.trim() || f.name.replace(/\.[^.]+$/, ""),
        project_hint: projectHint.trim() || undefined,
        participants: participants.trim() || undefined,
      });
      setStatus("extracting");
      await api.waitForExtraction(ingested.id);
      setStatus("done");
      setFile(null);
      if (fileRef.current) fileRef.current.value = "";
      setTimeout(onIngested, 1400);
    } catch (e) {
      setError(String(e));
      setStatus("error");
    }
  };

  // ── Drop-zone handlers ──────────────────────────────────────────────────────
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0];
    if (f) startUpload(f);
  };

  const handleDragOver = (e: React.DragEvent) => { e.preventDefault(); setDragging(true); };
  const handleDragLeave = (e: React.DragEvent) => { e.preventDefault(); setDragging(false); };
  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files?.[0];
    if (f) startUpload(f);
  };

  // ── Text ingest ─────────────────────────────────────────────────────────────
  const submitText = async () => {
    setError(null);
    setStatus("extracting");
    try {
      const ingested = await api.ingest({
        title: title.trim() || "Untitled meeting",
        content,
        project_hint: projectHint.trim() || undefined,
        participants: participants.split(",").map((p) => p.trim()).filter(Boolean) || undefined,
      });
      await api.waitForExtraction(ingested.id);
      setStatus("done");
      setContent("");
      setTimeout(onIngested, 900);
    } catch (e) {
      setError(String(e));
      setStatus("error");
    }
  };

  return (
    <div className="workspace-page upload-studio">
      <header><h2>Hand the agent a meeting</h2></header>

      <div className="upload-intro"><span className="upload-intro-icon"><Icon name="intelligence"/></span>
        <p className="muted" style={{ marginTop: 0 }}>
          Upload a <strong style={{ color: "var(--text)" }}>video or audio recording</strong> and
          the agent transcribes it with Whisper, then extracts{" "}
          <strong style={{ color: "var(--text)" }}>tasks, decisions, risks, and blockers</strong>{" "}
          — each with an owner, due date, priority, and verbatim source quote.{" "}
          <strong style={{ color: "var(--text)" }}>Zero manual tagging.</strong>
        </p>
      </div>

      {/* Mode tabs */}
      <div className="upload-modes" role="group" aria-label="Transcript source">
        {(["video", "text"] as Mode[]).map((m) => (
          <button
            key={m}
            className={mode === m ? "active" : ""}
            aria-pressed={mode === m}
            disabled={busy}
            onClick={() => { setMode(m); setStatus("idle"); setError(null); setFile(null); }}
          >
            <Icon name={m === "video" ? "upload" : "summary"}/>{m === "video" ? "Video / Audio file" : "Paste transcript text"}
          </button>
        ))}
      </div>

      <div className="panel">
        {/* Shared metadata */}
        <div className="form-row">
          <label htmlFor="upload-title">Meeting title<input id="upload-title" placeholder="Give your meeting a name" value={title} onChange={(e) => setTitle(e.target.value)} disabled={busy} /></label>
          <label htmlFor="upload-project">Project <span>(optional)</span><input id="upload-project" placeholder="Add a project" value={projectHint} onChange={(e) => setProjectHint(e.target.value)} disabled={busy} /></label>
        </div>
        <div className="form-row">
          <label htmlFor="upload-participants">Participants <span>(optional)</span><input
            id="upload-participants"
            placeholder="Names, separated by commas"
            value={participants}
            onChange={(e) => setParticipants(e.target.value)}
            disabled={busy}
          /></label>
        </div>

        {mode === "video" ? (
          <>
            {/* Drop zone */}
            <div
              className="upload-dropzone"
              role="button"
              tabIndex={busy ? -1 : 0}
              aria-disabled={busy}
              aria-label="Choose a meeting recording to upload"
              onKeyDown={(event) => { if (!busy && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); fileRef.current?.click(); } }}
              onClick={() => !busy && fileRef.current?.click()}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={!busy ? handleDrop : undefined}
              style={{
                border: `2px dashed ${dragging ? "var(--accent)" : status === "done" ? "var(--ok)" : "var(--border)"}`,
                borderRadius: 12,
                padding: "36px 24px",
                textAlign: "center",
                cursor: busy ? "default" : "pointer",
                background: "var(--panel-2)",
                transition: "border-color 0.15s, background 0.15s",
              }}
            >
              {dragging ? (
                <>
                  <span className="upload-drop-icon"><Icon name="upload"/></span>
                  <div style={{ marginTop: 8, fontWeight: 600, color: "var(--accent)" }}>Drop to start transcription</div>
                </>
              ) : status === "done" ? (
                <>
                  <span className="upload-drop-icon"><Icon name="check"/></span>
                  <div style={{ marginTop: 8, fontWeight: 600, color: "var(--ok)" }}>Transcription complete</div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>Redirecting to tasks…</div>
                </>
              ) : file && busy ? (
                <>
                  <span className="upload-drop-icon"><Icon name="meetings"/></span>
                  <div style={{ marginTop: 8, fontWeight: 600 }}>{file.name}</div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    {(file.size / 1024 / 1024).toFixed(1)} MB · processing…
                  </div>
                </>
              ) : (
                <>
                  <span className="upload-drop-icon"><Icon name="upload"/></span>
                  <div style={{ marginTop: 8, fontWeight: 600 }}>
                    {status === "error" ? "Drop another file to retry" : "Drop your meeting recording here"}
                  </div>
                  <div className="muted" style={{ fontSize: 12, marginTop: 4 }}>
                    mp4 · mp3 · m4a · wav · ogg · webm · flac · max 100 MB · transcription starts immediately
                  </div>
                </>
              )}
            </div>

            <input
              ref={fileRef}
              type="file"
              accept={ACCEPTED}
              style={{ display: "none" }}
              onChange={handleFileChange}
            />

            {/* Step progress strip — shown while processing or done */}
            {(busy || status === "done") && (
              <div style={{
                display: "flex",
                alignItems: "flex-start",
                marginTop: 16,
                padding: "14px 16px",
                background: "var(--panel-2)",
                borderRadius: 8,
                border: "1px solid var(--border)",
              }}>
                {STEPS.map((step, i) => {
                  const stepIdx = STEP_ORDER.indexOf(step.key);
                  const curIdx  = STEP_ORDER.indexOf(status);
                  const isActive = step.key === status;
                  const isDone   = status === "done" || stepIdx < curIdx;

                  return (
                    <div key={step.key} style={{ display: "flex", alignItems: "flex-start", flex: 1 }}>
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flex: 1 }}>
                        <div style={{
                          width: 28, height: 28, borderRadius: "50%",
                          display: "flex", alignItems: "center", justifyContent: "center",
                          fontSize: 13, fontWeight: 700,
                          background: isDone ? "var(--ok)" : isActive ? "var(--accent)" : "var(--border)",
                          color: isDone || isActive ? "#f7f8ec" : "var(--muted)",
                          transition: "background 0.3s",
                        }}>
                          {isDone ? <Icon name="check"/> : isActive ? <Spinner /> : i + 1}
                        </div>
                        <div style={{
                          marginTop: 6, fontSize: 11, textAlign: "center",
                          fontWeight: isActive ? 600 : 400,
                          color: isDone ? "var(--ok)" : isActive ? "var(--accent)" : "var(--muted)",
                        }}>
                          {step.label}
                          {isActive && step.hint && (
                            <div style={{ color: "var(--muted)", fontWeight: 400 }}>{step.hint}</div>
                          )}
                        </div>
                      </div>
                      {i < STEPS.length - 1 && (
                        <div style={{
                          height: 2, width: 24, marginTop: 13, flexShrink: 0,
                          background: isDone ? "var(--ok)" : "var(--border)",
                          transition: "background 0.3s",
                        }} />
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {/* Error banner */}
            {status === "error" && error && (
              <div style={{
                marginTop: 16, padding: "12px 16px",
                background: "rgba(239,68,68,0.1)",
                border: "1px solid var(--crit)",
                borderRadius: 8, color: "var(--crit)", fontSize: 13,
              }}>
                {error}
              </div>
            )}
          </>
        ) : (
          <>
            <label className="upload-transcript-label" htmlFor="upload-transcript">Meeting transcript</label>
            <textarea
              id="upload-transcript"
              disabled={busy}
              placeholder="Paste raw transcript here…"
              value={content}
              onChange={(e) => setContent(e.target.value)}
            />
            <div style={{ marginTop: 12, display: "flex", gap: 12, alignItems: "center" }}>
              <button
                className="primary"
                onClick={submitText}
                disabled={busy || content.trim().length < 20}
              >
                {status === "extracting" ? "Extracting…" : "Ingest & extract"}
              </button>
              {status === "error"  && error  && <span style={{ color: "var(--crit)" }}>{error}</span>}
              {status === "done"              && <span style={{ color: "var(--ok)" }}>Done — redirecting…</span>}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function Spinner() {
  return (
    <span style={{
      display: "inline-block", width: 12, height: 12,
      border: "2px solid currentColor", borderTopColor: "transparent",
      borderRadius: "50%", animation: "spin 0.7s linear infinite",
    }} />
  );
}
