import { useEffect, useState } from "react";
import { request, setWorkspace } from "../api";

type Account = { account_id: string; workspace_id: string; role: string; workspaces: { id: string; name: string; role: string }[] };
type Token = { id: string; name: string; expires_at: string; revoked_at: string | null };
const connectors: Record<string, [string, string][]> = {
  Discord: [["discord_webhook_url", "Channel webhook URL"]],
  "Microsoft Teams": [["teams_webhook_url", "Workflow webhook URL"]],
  Slack: [["slack_bot_token", "Bot token"], ["slack_default_channel", "Channel ID"]],
  Notion: [["notion_api_key", "Integration token"], ["notion_parent_page_id", "Parent page ID"]],
  Linear: [["linear_api_key", "API key"], ["linear_team_id", "Team ID"]],
  Jira: [["jira_base_url", "Cloud URL"], ["jira_email", "Account email"], ["jira_api_token", "API token"], ["jira_project_key", "Project key"]],
  Salesforce: [["salesforce_instance_url", "Org URL"], ["salesforce_client_id", "Connected App consumer key"], ["salesforce_client_secret", "Consumer secret"], ["salesforce_refresh_token", "Refresh token"]],
  "Google Calendar": [["google_calendar_client_id", "OAuth client ID"], ["google_calendar_client_secret", "OAuth client secret"], ["google_calendar_refresh_token", "Refresh token"], ["google_calendar_id", "Calendar ID"]],
};

export function WorkspaceSettings() {
  const [account, setAccount] = useState<Account | null>(null);
  const [tokens, setTokens] = useState<Token[]>([]);
  const [issued, setIssued] = useState("");
  const [message, setMessage] = useState("");
  const [provider, setProvider] = useState("Discord");
  const [values, setValues] = useState<Record<string, string>>({});
  const [status, setStatus] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  async function refresh() {
    try {
      const me = await request<Account>("/api/account/me"); setAccount(me);
      setWorkspace(me.workspace_id);
      setTokens(await request<Token[]>("/api/account/tokens"));
      const result = await request<Record<string, boolean>>("/api/integrations/status");
      setStatus(result);
    } catch { setMessage("Workspace settings are available when account authentication is configured."); }
  }
  useEffect(() => { void refresh(); }, []);
  async function action(work: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await work(); } catch { setMessage("Could not save this change. Check your access and the entered values."); } finally { setBusy(false); }
  }
  return <section className="workspace-settings"><h2>Workspace & connectors</h2>
    <p>Keep each team's meeting data and integrations together.</p>
    {message && <p role="status">{message}</p>}
    {account && <>
      <label>Active workspace <select value={account.workspace_id} onChange={event => { setWorkspace(event.target.value); void refresh(); }}>
        {account.workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</option>)}
      </select></label>
      <p>Your account ID: <code>{account.account_id}</code></p>
      <form onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void action(async () => { await request("/api/account/workspaces", { method: "POST", body: JSON.stringify({ name: form.get("name") }) }); await refresh(); }); }}>
        <label>New workspace <input name="name" required maxLength={128} /></label><button disabled={busy}>Create workspace</button>
      </form>
      {(account.role === "owner" || account.role === "admin") && <>
        <h3>Connect your tools</h3><p>Credentials are encrypted on the server and are never returned to the browser. Saving replaces the selected connector's values.</p>
        <p>{Object.entries(status).filter(([, enabled]) => enabled === true).map(([name]) => name.replace("_", " ")).join(", ") || "No connectors configured"}</p>
        <label>Connector <select value={provider} onChange={event => { setProvider(event.target.value); setValues({}); }}>{Object.keys(connectors).map(name => <option key={name}>{name}</option>)}</select></label>
        <form onSubmit={event => { event.preventDefault(); void action(async () => {
          const payload = Object.fromEntries(connectors[provider].map(([field]) => [field, values[field] ?? ""]));
          await request("/api/account/connectors", { method: "PUT", body: JSON.stringify({ values: payload }) }); setValues({}); setMessage(`${provider} saved.`); await refresh();
        }); }}>
          {connectors[provider].map(([field, label]) => <label key={field}>{label}<input type="password" autoComplete="off" value={values[field] ?? ""} onChange={event => setValues({ ...values, [field]: event.target.value })} /></label>)}
          <button disabled={busy}>Save {provider}</button><button type="button" disabled={busy} onClick={() => void action(async () => { await request("/api/account/connectors", { method: "PUT", body: JSON.stringify({ values: Object.fromEntries(connectors[provider].map(([field]) => [field, ""])) }) }); setMessage(`${provider} disconnected.`); })}>Disconnect</button>
        </form>
        <h3>Invite a teammate</h3><p>Ask them to sign in and share their account ID from this page.</p>
        <form onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void action(async () => { await request("/api/account/members", { method: "PUT", body: JSON.stringify({ account_id: form.get("account_id"), role: form.get("role") }) }); setMessage("Workspace membership saved."); }); }}>
          <label>Account ID <input name="account_id" required /></label><label>Role <select name="role"><option value="member">Member</option><option value="viewer">Viewer</option>{account.role === "owner" && <option value="admin">Administrator</option>}</select></label><button disabled={busy}>Add teammate</button>
        </form>
      </>}
      <h3>Browser extension access</h3><p>Create a workspace token and paste it into the extension's API key setting. Tokens expire after 30 days and can be revoked here.</p>
      {account.role !== "viewer" && <button disabled={busy} onClick={() => void action(async () => { const result = await request<{ token: string }>("/api/account/tokens", { method: "POST", body: JSON.stringify({ name: "Browser extension" }) }); setIssued(result.token); await refresh(); })}>Create extension token</button>}
      {issued && <div><p>Copy this token now. It will only be shown once.</p><input aria-label="New extension token" readOnly value={issued} /><button onClick={() => { setIssued(""); }}>Dismiss token</button></div>}
      <ul>{tokens.map(token => <li key={token.id}>{token.name} · {token.revoked_at ? "Revoked" : `Expires ${new Date(token.expires_at).toLocaleDateString()}`} {!token.revoked_at && <button disabled={busy} onClick={() => void action(async () => { await request(`/api/account/tokens/${token.id}`, { method: "DELETE" }); await refresh(); })}>Revoke</button>}</li>)}</ul>
    </>}
  </section>;
}
