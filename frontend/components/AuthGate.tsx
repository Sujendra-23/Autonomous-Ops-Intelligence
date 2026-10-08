"use client";

import { useEffect, useState } from "react";
import { authConfigured, getIdentity, restoreIdentity } from "@/lib/auth";

// Wraps the console. Without OIDC configured it renders children straight away (development mode).
export function AuthGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(!authConfigured);
  const [signedIn, setSignedIn] = useState(!authConfigured);
  const [authError, setAuthError] = useState("");
  useEffect(() => {
    const expire = () => setSignedIn(false);
    window.addEventListener("aoi-session-expired", expire);
    if (authConfigured) restoreIdentity().then(setSignedIn).catch(() => setAuthError("Sign-in could not be completed. Please try again.")).finally(() => setReady(true));
    return () => window.removeEventListener("aoi-session-expired", expire);
  }, []);
  if (!ready) return <main className="main"><p>Opening your workspace…</p></main>;
  if (!signedIn) return <main className="main"><h1>Your meeting intelligence studio</h1><p>Sign in to access your private workspace, notes and connectors.</p>{authError && <p role="alert">{authError}</p>}<button onClick={() => getIdentity()?.signinRedirect()}>Sign in / Create account</button></main>;
  return <>{children}</>;
}
