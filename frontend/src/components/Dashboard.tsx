import { useEffect, useState } from "react";
import { api, type DashboardCounts } from "../api";

export function Dashboard() {
  const [data, setData] = useState<DashboardCounts | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.dashboard()
      .then((d) => !cancelled && setData(d))
      .catch((e) => !cancelled && setError(String(e)));
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <>
      <header>
        <h2>Dashboard</h2>
        <button className="ghost" onClick={() => window.location.reload()}>
          Refresh
        </button>
      </header>
      {error && <div className="panel">Couldn't load dashboard: {error}</div>}
      {!data && !error && <div className="panel muted">Loading…</div>}
      {data && (
        <>
          <div className="grid">
            <Stat label="Transcripts" value={data.transcripts} />
            <Stat label="Projects" value={data.projects} />
            <Stat label="Decisions" value={data.decisions} />
            <Stat label="Open tasks" value={data.open_tasks} />
            <Stat
              label="Overdue tasks"
              value={data.overdue_tasks}
              tone={data.overdue_tasks ? "critical" : undefined}
            />
            <Stat
              label="Stalled tasks"
              value={data.stalled_tasks}
              tone={data.stalled_tasks ? "warning" : undefined}
            />
            <Stat
              label="Open blockers"
              value={data.open_blockers}
              tone={data.open_blockers ? "warning" : undefined}
            />
            <Stat label="Open risks" value={data.open_risks} />
          </div>
          <div className="panel">
            <h3>What the agent is doing</h3>
            <p className="muted">
              This is the live state of the autonomous agent. Every task,
              decision, and blocker above was extracted from a meeting
              transcript — with{" "}
              <strong style={{ color: "var(--text)" }}>zero manual tagging</strong>{" "}
              — and mirrored into your trackers automatically. The drift
              monitor keeps watching after the meeting ends; non-zero numbers
              in <em>Overdue</em>, <em>Stalled</em>, or <em>Open blockers</em>{" "}
              mean the agent has already flagged work that needs attention —
              jump to <strong>Intelligence</strong> to see it and fire
              reminders.
            </p>
          </div>
          <div className="panel" style={{ borderColor: "var(--accent-2)" }}>
            <h3>Security: first-class, not buried</h3>
            <p className="muted">
              Every API key is wrapped in{" "}
              <code>pydantic.SecretStr</code> so credentials cannot leak into
              logs, tracebacks, or settings dumps. Your{" "}
              <code>.env</code> is auto-created at mode <code>0600</code>{" "}
              (owner-only), and an optional macOS Keychain recipe in the
              README lets you keep the key off disk entirely. Trust is the
              product, not a side effect.
            </p>
          </div>
        </>
      )}
    </>
  );
}

function Stat({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "warning" | "critical";
}) {
  return (
    <div className={`card ${tone ?? ""}`}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
    </div>
  );
}
