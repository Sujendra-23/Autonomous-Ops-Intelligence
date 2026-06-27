import { useState } from "react";
import { api, type DriftItem } from "../api";

type SearchHit = {
  chunk_id: string;
  transcript_id: string;
  transcript_title: string;
  content: string;
  score: number;
};

export function Intelligence() {
  const [drift, setDrift] = useState<DriftItem[] | null>(null);
  const [running, setRunning] = useState(false);
  const [notify, setNotify] = useState(false);

  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const runDrift = async () => {
    setRunning(true);
    setError(null);
    try {
      const res = await api.runDrift(notify);
      setDrift(res.items);
    } catch (e) {
      setError(String(e));
    } finally {
      setRunning(false);
    }
  };

  const runSearch = async () => {
    if (!query.trim()) return;
    setSearching(true);
    setError(null);
    try {
      const res = await api.search(query.trim());
      setHits(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setSearching(false);
    }
  };

  return (
    <>
      <header>
        <h2>Intelligence</h2>
      </header>

      <div className="panel">
        <h3>Drift detection</h3>
        <p className="muted">
          Scans for overdue tasks, stalled work, high-priority items with no
          owner, and aged blockers. Toggle "send reminders" to also fire Slack
          messages (when configured).
        </p>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <button className="primary" disabled={running} onClick={runDrift}>
            {running ? "Scanning…" : "Run drift scan"}
          </button>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={notify}
              onChange={(e) => setNotify(e.target.checked)}
              style={{ width: "auto" }}
            />
            Send Slack reminders
          </label>
        </div>
        {drift && drift.length === 0 && (
          <div className="muted" style={{ marginTop: 12 }}>
            No drift detected. ✨
          </div>
        )}
        {drift && drift.length > 0 && (
          <table style={{ marginTop: 16 }}>
            <thead>
              <tr>
                <th>Kind</th>
                <th>Severity</th>
                <th>Title</th>
                <th>Detail</th>
              </tr>
            </thead>
            <tbody>
              {drift.map((d, i) => (
                <tr key={i}>
                  <td>
                    <span className="pill">{d.kind.replace("_", " ")}</span>
                  </td>
                  <td>
                    <span className={`pill severity-${d.severity}`}>{d.severity}</span>
                  </td>
                  <td>{d.title}</td>
                  <td className="muted">{d.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h3>Semantic search</h3>
        <p className="muted">
          Vector search over transcript chunks. Useful for "did anyone mention
          X?" questions.
        </p>
        <div style={{ display: "flex", gap: 8 }}>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="What did we decide about the migration?"
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
          />
          <button className="primary" disabled={searching} onClick={runSearch}>
            {searching ? "…" : "Search"}
          </button>
        </div>
        {hits.length > 0 && (
          <div style={{ marginTop: 16 }}>
            {hits.map((h) => (
              <div key={h.chunk_id} className="panel" style={{ background: "var(--panel-2)" }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <strong>{h.transcript_title}</strong>
                  <span className="muted">score {h.score.toFixed(3)}</span>
                </div>
                <div className="muted" style={{ marginTop: 8 }}>
                  {h.content.slice(0, 600)}
                  {h.content.length > 600 ? "…" : ""}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {error && <div className="panel">Error: {error}</div>}
    </>
  );
}
