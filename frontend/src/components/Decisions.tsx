import { useEffect, useState } from "react";
import { api, type Decision } from "../api";

export function Decisions() {
  const [items, setItems] = useState<Decision[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .decisions()
      .then((d) => setItems(d.items))
      .catch((e) => setError(String(e)));
  }, []);

  return (
    <>
      <header>
        <h2>Decisions</h2>
      </header>
      {error && <div className="panel">Error: {error}</div>}
      {items.length === 0 && !error && (
        <div className="panel muted">No decisions captured yet.</div>
      )}
      {items.map((d) => (
        <div className="panel" key={d.id}>
          <div style={{ fontWeight: 600 }}>{d.summary}</div>
          {d.rationale && <div className="muted" style={{ marginTop: 6 }}>{d.rationale}</div>}
          {d.decided_by && d.decided_by.length > 0 && (
            <div style={{ marginTop: 8 }}>
              {d.decided_by.map((p) => (
                <span key={p} className="pill" style={{ marginRight: 6 }}>
                  {p}
                </span>
              ))}
            </div>
          )}
          {d.source_quote && <div className="quote">"{d.source_quote}"</div>}
          <div className="muted" style={{ marginTop: 8, fontSize: 12 }}>
            {new Date(d.created_at).toLocaleString()}
          </div>
        </div>
      ))}
    </>
  );
}
