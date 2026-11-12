import { useState } from "react";
import { authConfigured, gatewayAuth, identity } from "../auth";
import { BrandMark, Icon } from "./Icon";
import { CompanySignIn } from "./CompanySignIn";

export function SignIn({
  onSignedIn,
  error: initialError = "",
}: {
  onSignedIn: () => void;
  error?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError);
  if (gatewayAuth) return <CompanySignIn onSignedIn={onSignedIn} />;
  async function login() {
    if (!identity) return;
    setBusy(true);
    setError("");
    try {
      await identity.signinRedirect({
        state: {
          returnTo:
            window.location.pathname === "/login"
              ? "/"
              : window.location.pathname + window.location.search,
        },
      });
    } catch {
      setBusy(false);
      setError(
        "Could not open company sign-in. Check your connection and try again.",
      );
    }
  }
  return (
    <main className="company-sign-in">
      <section className="company-sign-in-intro">
        <div className="login-brand">
          <BrandMark />
          <span>aoi.</span>
        </div>
        <h1>
          Your work.
          <br />
          <em>One place to return.</em>
        </h1>
        <p>
          Meetings, decisions, and next steps, together in your company
          workspace.
        </p>
      </section>
      <section className="company-sign-in-form">
        <h2>Login to your workspace.</h2>
        {authConfigured ? (
          <>
            <p>Continue with your company’s sign-in provider.</p>
            <button
              className="primary"
              disabled={busy}
              onClick={() => void login()}
            >
              {busy ? "Opening login…" : "Login"}
              <Icon name="arrow" />
            </button>
          </>
        ) : (
          <>
            <p>
              This installation is using a local workspace. Account login is not
              enabled yet.
            </p>
            <p>
              Your administrator needs to connect company sign-in before you can
              log in with an account.
            </p>
            <a className="primary local-workspace-link" href="/">
              Back to local workspace
              <Icon name="arrow" />
            </a>
          </>
        )}
        {error && (
          <p className="load-error" role="alert">
            {error}
          </p>
        )}
        <p className="login-help">
          Need access? Contact your company administrator.
        </p>
        <a href="/pricing">See plans & pricing</a>
      </section>
    </main>
  );
}
