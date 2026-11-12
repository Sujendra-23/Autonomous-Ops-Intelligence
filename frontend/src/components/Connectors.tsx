import { useEffect, useRef, useState } from "react";
import { request } from "../api";

type Connector = {
  name: string;
  oauth_available: boolean;
  setup_error?: string | null;
  status: "connected" | "configured" | "disconnected" | "select_destination";
  destination: string | null;
  choices: { id: string; label: string }[];
};
type Attempt = { provider: string; ticket: string; expiresAt: number };
type AttemptResult = { status: "pending" | "connected" | "select_destination" | "failed"; message?: string };
const purposes: Record<string, string> = {
  google_calendar: "Schedule Google Meet calls, invite guests, and use your calendar’s meeting details.",
  slack: "Send meeting summaries and task reminders to your channel.",
  notion: "Publish meeting notes and project pages to your workspace.",
  linear: "Bring existing team issues into Tasks and keep linked statuses in sync.",
  jira: "Bring existing project issues into Tasks and keep linked statuses in sync.",
  discord: "Post meeting summaries and next steps to your channel.",
  teams: "Post meeting summaries to your Microsoft Teams channel.",
};
function errorMessage(error: unknown) {
  if (error instanceof Error) {
    try {
      const detail = JSON.parse(error.message.slice(error.message.indexOf("{"))).detail;
      if (typeof detail === "string") return detail;
    } catch { /* Network errors do not have a JSON response. */ }
  }
  return "Could not reach the connection service. Check your connection and try again.";
}

export function Connectors({ canManage }: { canManage: boolean }) {
  const [connectors, setConnectors] = useState<Record<string, Connector>>({});
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [feedbackProvider, setFeedbackProvider] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [attempt, setAttempt] = useState<Attempt | null>(null);
  const [selections, setSelections] = useState<Record<string, string>>({});
  const popup = useRef<Window | null>(null);
  const alive = useRef(true);
  const generation = useRef(0);
  async function refresh() {
    const result = await request<Record<string, Connector>>("/api/connectors", { signal: AbortSignal.timeout(15000) });
    if (alive.current) setConnectors(result);
  }
  useEffect(() => {
    alive.current = true;
    void refresh().catch(err => { if (alive.current) setError(errorMessage(err)); }).finally(() => { if (alive.current) setLoading(false); });
    return () => { alive.current = false; generation.current++; popup.current?.close(); };
  }, []);
  useEffect(() => {
    if (!attempt) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      if (!attempt || stopped) return;
      if (Date.now() >= attempt.expiresAt) {
        setError("Sign-in timed out. Click Connect to start again."); setAttempt(null); setBusy(null); popup.current?.close(); return;
      }
      try {
        const result = await request<AttemptResult>(`/api/connectors/attempts/${attempt.ticket}`, { signal: AbortSignal.any([controller.signal, AbortSignal.timeout(15000)]) });
        if (stopped) return;
        if (result.status !== "pending") {
          setAttempt(null); setBusy(null); popup.current?.close();
          if (result.status === "failed") setError(result.message || "Sign-in failed. Please try again.");
          else { setMessage(result.message || "Sign-in complete."); await refresh(); }
          return;
        }
      } catch (err) {
        if (stopped) return;
        setError(errorMessage(err));
      }
      timer = setTimeout(() => void poll(), 2000);
    }
    void poll();
    return () => { stopped = true; controller.abort(); clearTimeout(timer); };
  }, [attempt]);
  async function connect(provider: string) {
    const current = ++generation.current;
    setFeedbackProvider(provider);
    setBusy(provider); setError(""); setMessage(""); setAttempt(null);
    if (!connectors[provider].oauth_available) {
      setBusy(null);
      setError(connectors[provider].setup_error || `${connectors[provider].name} sign-in needs to be configured by your workspace administrator.`);
      return;
    }
    // Open during the click gesture so browsers allow the authentication tab.
    try {
      popup.current?.close();
      popup.current = window.open("about:blank", "_blank");
      if (!popup.current) { setBusy(null); setError("Allow pop-ups for this site, then click Connect again."); return; }
      popup.current.opener = null;
      const result = await request<{ authorization_url: string; ticket: string; expires_in: number }>(`/api/connectors/${provider}/start`, { method: "POST", signal: AbortSignal.timeout(15000) });
      if (!alive.current || current !== generation.current) return;
      popup.current.location.href = result.authorization_url;
      setAttempt({ provider, ticket: result.ticket, expiresAt: Date.now() + result.expires_in * 1000 });
      setMessage(`Finish signing in to ${connectors[provider].name} in the authentication tab.`);
    } catch (err) {
      if (!alive.current || current !== generation.current) return;
      popup.current?.close(); setBusy(null); setError(errorMessage(err));
    }
  }
  async function change(provider: string, disconnect = false) {
    setFeedbackProvider(provider);
    setBusy(provider); setError(""); setMessage("");
    try {
      await request(`/api/connectors/${provider}${disconnect ? "" : "/destination"}`, {
        method: disconnect ? "DELETE" : "POST",
        ...(disconnect ? {} : { body: JSON.stringify({ id: selections[provider] }) }),
        signal: AbortSignal.timeout(30000),
      });
      if (!alive.current) return;
      await refresh();
      setMessage(disconnect ? `${connectors[provider].name} disconnected.` : "Destination saved.");
    } catch (err) { if (alive.current) setError(errorMessage(err)); }
    finally { if (alive.current) setBusy(null); }
  }
  return <section className="connections" aria-labelledby="connectors-title">
    <div className="section-heading"><h3 id="connectors-title">Connect your tools</h3>
      <button className="secondary" disabled={loading || Boolean(busy)} onClick={() => { setLoading(true); setFeedbackProvider(null); setMessage(""); setError(""); void refresh().catch(err => setError(errorMessage(err))).finally(() => setLoading(false)); }}>Refresh</button>
    </div>
    <p>Choose Connect, sign in, and approve access. Your workspace will save the connection automatically.</p>
    {!canManage && <p className="muted">Ask a workspace administrator to connect or change your tools.</p>}
    <div role="status" aria-live="polite">{loading ? <p>Loading connectors…</p> : !feedbackProvider && message && <p>{message}</p>}</div>
    {!feedbackProvider && error && <p className="connection-error" role="alert">{error}</p>}
    <ul className="connector-rows">{Object.entries(connectors).map(([provider, connector]) => {
      const connected = connector.status === "connected" || connector.status === "configured";
      const pending = connector.status === "select_destination";
      return <li key={provider}>
        <div className="connector-row"><div className="connector-copy"><h4>{connector.name}</h4><p>{purposes[provider]}</p>
          <span className={connected ? "connection-state connected" : "connection-state"}>{connected ? `${connector.status === "connected" ? "Connected" : "Configured"}${connector.destination ? ` · ${connector.destination}` : ""}` : pending ? "Choose a destination" : !connector.oauth_available ? "Setup required" : "Not connected"}</span>
        </div>
        {canManage && <div className="connector-actions"><button disabled={Boolean(busy)} onClick={() => void connect(provider)} aria-label={`${connected || pending ? "Reconnect" : "Connect"} ${connector.name}`}>{busy === provider && attempt ? "Waiting for sign-in…" : busy === provider ? "Connecting…" : connected || pending ? "Reconnect" : "Connect"}</button>
          {(connected || pending) && <button className="secondary" disabled={Boolean(busy)} onClick={() => void change(provider, true)}>Disconnect</button>}
        </div>}</div>
        {feedbackProvider === provider && <div aria-live="polite">
          {error ? <p className="connection-error" role="alert">{error}</p> : message ? <p role="status">{message}</p> : busy === provider ? <p role="status">Contacting the connection service…</p> : null}
        </div>}
        {!connector.oauth_available && !(feedbackProvider === provider && error) && <p className="muted connector-hint">{connector.setup_error || "A workspace administrator needs to configure this provider’s sign-in app."}</p>}
        {pending && canManage && <form className="destination-form" onSubmit={event => { event.preventDefault(); void change(provider); }}>
          <label htmlFor={`destination-${provider}`}>Destination for {connector.name}</label>
          <select id={`destination-${provider}`} required value={selections[provider] || ""} onChange={event => setSelections({ ...selections, [provider]: event.target.value })} disabled={Boolean(busy)}>
            <option value="" disabled>Choose a destination</option>{connector.choices.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
          </select><button disabled={Boolean(busy) || !selections[provider]}>Use destination</button>
        </form>}
      </li>;
    })}</ul>
    {attempt && <button className="secondary" onClick={() => { generation.current++; popup.current?.close(); setAttempt(null); setBusy(null); setMessage("Stopped waiting for sign-in. Refresh to check whether it completed."); }}>Stop waiting</button>}
    <p className="muted">Connections apply to this workspace. When a provider offers several destinations, choose where your notes or tasks should go.</p>
  </section>;
}
