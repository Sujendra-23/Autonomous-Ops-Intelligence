import { useEffect, useState } from "react";
import { api, type SummaryEvidence, type WeeklySummary as Summary } from "../api";
import "./weekly-summary.css";

const dateFormat = new Intl.DateTimeFormat(undefined, {
  month: "short", day: "numeric", year: "numeric",
});
const date = (value: string) => dateFormat.format(new Date(value));

export function WeeklySummary({ onNavigate }: {
  onNavigate: (view: "tasks" | "decisions" | "upload") => void;
}) {
  const [data, setData] = useState<Summary | null>(null);
  const [error, setError] = useState(false);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setData(null);
    setError(false);
    api.weeklySummary(controller.signal)
      .then((summary) => { if (!controller.signal.aborted) setData(summary); })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [revision]);

  const loading = !data && !error;
  return (
    <div className="weekly-summary">
      <header className="weekly-toolbar">
        <div>
          <h2>Weekly summary</h2>
          <p>Meeting intelligence for your leadership check-in.</p>
        </div>
        <div className="weekly-actions">
          <button className="ghost" disabled={loading} onClick={() => setRevision((n) => n + 1)}>
            Refresh
          </button>
          <button className="primary" disabled={!data} onClick={() => window.print()}>
            Print / Save PDF
          </button>
        </div>
      </header>

      {loading && <div className="panel muted" role="status">Generating your briefing from workspace records…</div>}
      {error && <div className="panel" role="alert">
        <h3>Couldn’t load the weekly summary</h3>
        <p>Check your connection and workspace access, then try Refresh.</p>
      </div>}

      {data && <article className="weekly-report" aria-label="Weekly leadership briefing">
        <div className="weekly-report-heading">
          <div>
            <h3>Leadership briefing</h3>
            <p><time dateTime={data.period_start}>{date(data.period_start)}</time> to{" "}
              <time dateTime={data.period_end}>{date(data.period_end)}</time></p>
          </div>
          <p className="weekly-generated">Updated <time dateTime={data.generated_at}>
            {new Date(data.generated_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", timeZoneName: "short" })}
          </time></p>
        </div>

        <div className="weekly-overview">
          <p className="weekly-verdict">
            {data.open_risk_count || data.overdue_task_count
              ? "Unresolved work needs attention."
              : "No open risks or overdue tasks."}
          </p>
          <p className="weekly-context">Risks and tasks reflect the current workspace. Decisions cover the last seven days.</p>
          <dl className="weekly-totals">
            <div><dt>Open risks</dt><dd>{data.open_risk_count}</dd></div>
            <div><dt>Overdue tasks</dt><dd>{data.overdue_task_count}</dd></div>
            <div><dt>Decisions captured</dt><dd>{data.decision_count}</dd></div>
          </dl>
        </div>

        <div className="weekly-attention">
          <section aria-labelledby="weekly-risks">
            <SectionHeading id="weekly-risks" title="Open risks" shown={data.open_risks.length} total={data.open_risk_count} />
            <p className="weekly-section-note">Highest severity first</p>
            {data.open_risks.length === 0 && <p className="weekly-empty">No open risks recorded.</p>}
            <ul className="weekly-items">
              {data.open_risks.map((risk) => <li key={risk.id}>
                <div className="weekly-item-meta">
                  <span className={`weekly-label weekly-tone-${risk.severity}`}>{risk.severity} risk</span>
                  <span>{risk.project_name || "No project assigned"}</span>
                </div>
                <h4>{risk.title}</h4>
                <p className="weekly-item-detail"><strong>Mitigation: </strong>{risk.mitigation || "No mitigation recorded."}</p>
                <Evidence item={risk} title={risk.title} detail={risk.description} />
              </li>)}
            </ul>
          </section>

          <section aria-labelledby="weekly-overdue">
            <SectionHeading id="weekly-overdue" title="Overdue tasks" shown={data.overdue_tasks.length} total={data.overdue_task_count} />
            <p className="weekly-section-note">Highest priority first, then oldest due date</p>
            {data.overdue_tasks.length === 0 && <p className="weekly-empty">No active tasks are overdue.</p>}
            <ul className="weekly-items">
              {data.overdue_tasks.map((task) => <li key={task.id}>
                <div className="weekly-item-meta">
                  <span className={`weekly-label weekly-tone-${task.priority}`}>{task.priority} priority</span>
                  <span>{task.project_name || "No project assigned"}</span>
                </div>
                <h4>{task.title}</h4>
                <p className="weekly-item-detail"><strong>{task.owner || "Unassigned"}</strong>{" · "}
                  {task.status.replaceAll("_", " ")}{" · Due "}<time dateTime={task.due_date}>{date(task.due_date)}</time></p>
                <Evidence item={task} title={task.title} />
              </li>)}
            </ul>
            {data.overdue_task_count > 0 && <button className="weekly-link" onClick={() => onNavigate("tasks")}>Review tasks</button>}
          </section>
        </div>

        <section className="weekly-decisions" aria-labelledby="weekly-decisions">
          <SectionHeading id="weekly-decisions" title="Decisions captured this week" shown={data.decisions.length} total={data.decision_count} />
          <p className="weekly-section-note">Most recently captured first; superseded decisions excluded</p>
          {data.decisions.length === 0 && <p className="weekly-empty">No current decisions captured in the last seven days.</p>}
          <ul className="weekly-items">
            {data.decisions.map((decision) => <li key={decision.id}>
              <div className="weekly-item-meta">
                <span>{decision.project_name || "No project assigned"}</span>
                <time dateTime={decision.created_at}>{date(decision.created_at)}</time>
              </div>
              <h4>{decision.summary}</h4>
              <p className="weekly-item-detail">{decision.rationale || "No rationale recorded."}</p>
              <p className="weekly-decision-owner">Decided by {decision.decided_by?.length ? decision.decided_by.join(", ") : "not recorded"}</p>
              <Evidence item={decision} title={decision.summary} detail={decision.rationale} />
            </li>)}
          </ul>
          {data.decision_count > 0 && <button className="weekly-link" onClick={() => onNavigate("decisions")}>Review decisions</button>}
        </section>

        {data.open_risk_count === 0 && data.overdue_task_count === 0 && data.decision_count === 0 &&
          <div className="weekly-get-started">
            <p>Ingest a meeting transcript to build your workspace’s tasks, risks, and decisions.</p>
            <button className="ghost" onClick={() => onNavigate("upload")}>Upload transcript</button>
          </div>}

        <footer className="weekly-footer">
          <p>Autonomous Ops Intelligence · Generated from existing workspace records.</p>
          <p>Up to three highlights per section. Totals include all matching records. This is a current snapshot, not a historical comparison.</p>
        </footer>
      </article>}
    </div>
  );
}

function SectionHeading({ id, title, shown, total }: {
  id: string; title: string; shown: number; total: number;
}) {
  return <div className="weekly-section-heading">
    <h3 id={id}>{title}</h3>
    <span>{total > shown ? `${shown} of ${total}` : `${total} total`}</span>
  </div>;
}

function Evidence({ item, title, detail }: {
  item: SummaryEvidence; title: string; detail?: string | null;
}) {
  return <details className="weekly-evidence">
    <summary aria-label={`View evidence for ${title}`}>View evidence</summary>
    <p><strong>{title}</strong></p>
    {detail && <p>{detail}</p>}
    {item.source_quote ? <blockquote>{item.source_quote}</blockquote> : <p>No source quote recorded.</p>}
    {item.confidence !== null && <p>Extraction confidence: {Math.round(item.confidence * 100)}%</p>}
    <p>Record: <code>{item.id}</code></p>
    {item.transcript_id && <p>Source transcript: <code>{item.transcript_id}</code></p>}
  </details>;
}
