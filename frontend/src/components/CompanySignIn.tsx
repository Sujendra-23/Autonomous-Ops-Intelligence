import { useState, type FormEvent } from "react";
import { gatewaySignIn } from "../auth";
import { BrandMark, Icon } from "./Icon";

export function CompanySignIn({ onSignedIn }: { onSignedIn: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError("");
    try {
      await gatewaySignIn(String(form.get("company")).trim().toLowerCase(), String(form.get("email")).trim(), String(form.get("password")));
      onSignedIn();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Sign-in could not be completed."); }
    finally { setBusy(false); }
  }
  return <main className="company-sign-in"><section className="company-sign-in-intro"><div className="login-brand"><BrandMark/><span>aoi.</span></div><h1>Your company.<br/><em>Your next chapter.</em></h1><p>One home for your meetings, decisions, and next steps. A workspace that belongs to your company.</p><span className="login-privacy"><Icon name="tasks"/>Separate company workspaces. Shared team momentum.</span></section><section className="company-sign-in-form"><h2>Welcome to your workspace.</h2><p>Sign in with your company’s Identity Access Gateway account.</p><form onSubmit={submit}><label htmlFor="company-code">Company code</label><input disabled={busy} id="company-code" name="company" autoComplete="organization" placeholder="e.g. acme" pattern="[a-zA-Z0-9][a-zA-Z0-9-]{1,62}" required/><label htmlFor="company-email">Work email</label><input disabled={busy} id="company-email" name="email" type="email" autoComplete="username" required/><label htmlFor="company-password">Password</label><input disabled={busy} id="company-password" name="password" type="password" autoComplete="current-password" required/>{error && <p className="load-error" role="alert">{error}</p>}<button className="primary" disabled={busy}>{busy ? "Opening your workspace…" : "Login to your company"}<Icon name="arrow"/></button></form><p className="login-help">Need access? Ask your company administrator for your company code and account.</p><a href="/pricing">See plans & pricing</a></section></main>;
}
