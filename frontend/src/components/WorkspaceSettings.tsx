import { useEffect, useState } from "react";
import { request, setWorkspace } from "../api";
import { authConfigured, copyGatewaySession, gatewayAuth } from "../auth";
import { Connectors } from "./Connectors";

type Account = { account_id: string; workspace_id: string; role: string; workspaces: { id: string; name: string; role: string }[] };
type Token = { id: string; name: string; expires_at: string; revoked_at: string | null };
export function WorkspaceSettings() {
  const [account, setAccount] = useState<Account | null>(null);
  const [tokens, setTokens] = useState<Token[]>([]);
  const [issued, setIssued] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  async function refresh() {
    try {
      const me = await request<Account>("/api/account/me"); setAccount(me);
      setWorkspace(me.workspace_id);
      if (!gatewayAuth) setTokens(await request<Token[]>("/api/account/tokens"));
      window.dispatchEvent(new Event("aoi-workspace-updated"));
    } catch { setMessage("Workspace settings are available when account authentication is configured."); }
  }
  useEffect(() => { if (authConfigured) void refresh(); }, []);
  async function action(work: () => Promise<void>) {
    setBusy(true); setMessage("");
    try { await work(); } catch { setMessage("Could not save this change. Check your access and the entered values."); } finally { setBusy(false); }
  }
  return <section className="workspace-settings"><h2>Workspace & connectors</h2>
    <p>Keep each team's meeting data and integrations together.</p>
    {message && <p role="status">{message}</p>}
    {account && <label>Active workspace <select value={account.workspace_id} onChange={event => { setWorkspace(event.target.value); void refresh(); }}>
        {account.workspaces.map(workspace => <option key={workspace.id} value={workspace.id}>{workspace.name} · {workspace.role}</option>)}
      </select></label>}
    <Connectors key={account?.workspace_id || "development"} canManage={!authConfigured || account?.role === "owner" || account?.role === "admin"} />
    {account && <>
      <p>Your account ID: <code>{account.account_id}</code></p>
      {(account.role === "owner" || account.role === "admin") && <form onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void action(async () => { await request("/api/account/workspace", { method: "PATCH", body: JSON.stringify({ name: form.get("name") }) }); await refresh(); setMessage("Workspace name saved."); }); }}><label>Company workspace name<input name="name" defaultValue={account.workspaces.find(workspace => workspace.id === account.workspace_id)?.name} required maxLength={128}/></label><button disabled={busy}>Save workspace name</button></form>}
      {gatewayAuth && <p>Your company and member roles are managed in the Identity Access Gateway. Connector settings and meeting data are kept separately in this company’s workspace.</p>}
      {!gatewayAuth && <>
      <form onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void action(async () => { await request("/api/account/workspaces", { method: "POST", body: JSON.stringify({ name: form.get("name") }) }); await refresh(); }); }}>
        <label>New workspace <input name="name" required maxLength={128} /></label><button disabled={busy}>Create workspace</button>
      </form>
      {(account.role === "owner" || account.role === "admin") && <>
        <h3>Invite a teammate</h3><p>Ask them to sign in and share their account ID from this page.</p>
        <form onSubmit={event => { event.preventDefault(); const form = new FormData(event.currentTarget); void action(async () => { await request("/api/account/members", { method: "PUT", body: JSON.stringify({ account_id: form.get("account_id"), role: form.get("role") }) }); setMessage("Workspace membership saved."); }); }}>
          <label>Account ID <input name="account_id" required /></label><label>Role <select name="role"><option value="member">Member</option><option value="viewer">Viewer</option>{account.role === "owner" && <option value="admin">Administrator</option>}</select></label><button disabled={busy}>Add teammate</button>
        </form>
      </>}
      </>}
      <h3>Browser extension access</h3>
      {gatewayAuth ? <><p>Copy your current company session token into the extension’s API key field. It is short lived: copy a fresh token before starting a capture after it expires. The backend checks its company and session with the gateway.</p>{account.role !== "viewer" && <button disabled={busy} onClick={() => void action(async () => { await copyGatewaySession(); setMessage("Company session token copied. Paste it into the extension’s API key field."); })}>Copy extension session token</button>}</> : <>
      <p>Create a workspace token and paste it into the extension's API key setting. Tokens expire after 30 days and can be revoked here.</p>
      {account.role !== "viewer" && <button disabled={busy} onClick={() => void action(async () => { const result = await request<{ token: string }>("/api/account/tokens", { method: "POST", body: JSON.stringify({ name: "Browser extension" }) }); setIssued(result.token); await refresh(); })}>Create extension token</button>}
      {issued && <div><p>Copy this token now. It will only be shown once.</p><input aria-label="New extension token" readOnly value={issued} /><button onClick={() => { setIssued(""); }}>Dismiss token</button></div>}
      <ul>{tokens.map(token => <li key={token.id}>{token.name} · {token.revoked_at ? "Revoked" : `Expires ${new Date(token.expires_at).toLocaleDateString()}`} {!token.revoked_at && <button disabled={busy} onClick={() => void action(async () => { await request(`/api/account/tokens/${token.id}`, { method: "DELETE" }); await refresh(); })}>Revoke</button>}</li>)}</ul>
      </>}
    </>}
  </section>;
}
