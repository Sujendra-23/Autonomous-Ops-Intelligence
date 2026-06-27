import { useEffect, useState } from "react";
import { api, type Task } from "../api";

const STATUSES = ["", "open", "in_progress", "blocked", "done", "cancelled"];

export function Tasks() {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [status, setStatus] = useState<string>("");
  const [overdueOnly, setOverdueOnly] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    try {
      const res = await api.tasks({
        status: status || undefined,
        overdue: overdueOnly,
      });
      setTasks(res.items);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, overdueOnly]);

  const setTaskStatus = async (id: string, next: string) => {
    await api.updateTask(id, { status: next as Task["status"] });
    void load();
  };

  return (
    <>
      <header>
        <h2>Tasks</h2>
        <div style={{ display: "flex", gap: 12 }}>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s ? s : "All statuses"}
              </option>
            ))}
          </select>
          <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
            <input
              type="checkbox"
              checked={overdueOnly}
              onChange={(e) => setOverdueOnly(e.target.checked)}
              style={{ width: "auto" }}
            />
            Overdue only
          </label>
        </div>
      </header>
      {error && <div className="panel">Error: {error}</div>}
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead>
            <tr>
              <th>Title</th>
              <th>Owner</th>
              <th>Status</th>
              <th>Priority</th>
              <th>Due</th>
              <th>Conf.</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {tasks.length === 0 && (
              <tr>
                <td colSpan={7} className="muted" style={{ padding: 24 }}>
                  No tasks for this filter.
                </td>
              </tr>
            )}
            {tasks.map((t) => (
              <tr key={t.id}>
                <td>
                  <div>{t.title}</div>
                  {t.source_quote && (
                    <div className="quote">"{t.source_quote}"</div>
                  )}
                </td>
                <td>{t.owner ?? <span className="muted">—</span>}</td>
                <td>
                  <span className={`pill status-${t.status}`}>{t.status}</span>
                </td>
                <td>
                  <span className={`pill priority-${t.priority}`}>{t.priority}</span>
                </td>
                <td>
                  {t.due_date
                    ? new Date(t.due_date).toLocaleDateString()
                    : <span className="muted">—</span>}
                </td>
                <td>
                  {t.confidence != null
                    ? `${Math.round(t.confidence * 100)}%`
                    : <span className="muted">—</span>}
                </td>
                <td>
                  <select
                    value={t.status}
                    onChange={(e) => setTaskStatus(t.id, e.target.value)}
                  >
                    {STATUSES.filter(Boolean).map((s) => (
                      <option key={s} value={s}>
                        {s}
                      </option>
                    ))}
                  </select>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
