import { useEffect, useState } from "react";
import { api } from "./api";
import { Dashboard } from "./components/Dashboard";
import { TranscriptUpload } from "./components/TranscriptUpload";
import { Tasks } from "./components/Tasks";
import { Decisions } from "./components/Decisions";
import { Intelligence } from "./components/Intelligence";
import { WeeklySummary } from "./components/WeeklySummary";
import { Meetings } from "./components/Meetings";
import { ExtensionSetup } from "./components/ExtensionSetup";
import { readRoute, type View } from "./navigation";
import { WorkspaceShell } from "./components/WorkspaceShell";

import { authConfigured, gatewayAuth, identity, restoreIdentity } from "./auth";
import { CompanySignIn } from "./components/CompanySignIn";
import { WorkspaceSettings } from "./components/WorkspaceSettings";
import { Roadmaps } from "./components/Roadmaps";
import { Pricing } from "./components/Pricing";

export function App() {
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
  if (!signedIn && window.location.pathname === "/pricing") return <main className="main"><Pricing onBack={() => window.location.assign("/")} /></main>;
  if (!signedIn && gatewayAuth) return <CompanySignIn onSignedIn={() => setSignedIn(true)} />;
  if (!signedIn) return <main className="main"><h1>Your meeting intelligence studio</h1><p>Sign in to access your private workspace, notes and connectors.</p>{authError && <p role="alert">{authError}</p>}<button onClick={() => identity?.signinRedirect({ state: { returnTo: window.location.pathname } })}>Sign in / Create account</button><p><a href="/pricing">View pricing</a></p></main>;
  return <WorkspaceApp />;
}

function WorkspaceApp() {
  const [route, setRoute] = useState(readRoute);
  const { view } = route;
  useEffect(() => {
    const onPopState = () => setRoute(readRoute());
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);
  function navigate(next: View, meetingId?: string) {
    const path = next === "dashboard" ? "/" : `/${next}${next === "meetings" && meetingId ? `/${encodeURIComponent(meetingId)}` : ""}`;
    if (window.location.pathname !== path) window.history.pushState({}, "", path);
    setRoute({ view: next, meetingId });
    window.scrollTo(0, 0);
  }
  const liveCount = useLiveMeetings();

  return <WorkspaceShell view={view} liveCount={liveCount} onNavigate={navigate}>
    {view === "settings" && <WorkspaceSettings />}
    {view === "pricing" && <Pricing />}
    {view === "roadmaps" && <Roadmaps />}
    {view === "dashboard" && <Dashboard onNavigate={navigate} />}
    {view === "meetings" && <Meetings meetingId={route.meetingId} onNavigate={navigate} />}
    {view === "extension" && <ExtensionSetup onNavigate={navigate} />}
    {view === "weekly-summary" && <WeeklySummary onNavigate={navigate} />}
    {view === "upload" && <TranscriptUpload onIngested={() => navigate("tasks")} />}
    {view === "tasks" && <Tasks onNavigate={navigate} />}
    {view === "decisions" && <Decisions />}
    {view === "intelligence" && <Intelligence />}
  </WorkspaceShell>;
}

// Poll for transcripts currently being recorded so the console shows a live
// indicator while the browser-extension note-taker is running.
function useLiveMeetings(): number {
  const [count, setCount] = useState(0);
  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const res = await api.transcripts();
        if (active) setCount(res.items.filter((t) => t.status === "live").length);
      } catch {
        /* transient errors are fine; try again next tick */
      }
    };
    poll();
    const id = setInterval(poll, 10000);
    return () => {
      active = false;
      clearInterval(id);
    };
  }, []);
  return count;
}
