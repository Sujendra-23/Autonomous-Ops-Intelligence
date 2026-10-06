import { useEffect, useState } from "react";
import { api, setApiKey } from "./api";
import { Dashboard } from "./components/Dashboard";
import { TranscriptUpload } from "./components/TranscriptUpload";
import { Tasks } from "./components/Tasks";
import { Decisions } from "./components/Decisions";
import { Intelligence } from "./components/Intelligence";

import { authConfigured, identity, restoreIdentity } from "./auth";
import { WorkspaceSettings } from "./components/WorkspaceSettings";

type View = "dashboard" | "upload" | "tasks" | "decisions" | "intelligence" | "settings";

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
  if (!signedIn) return <main className="main"><h1>Your meeting intelligence studio</h1><p>Sign in to access your private workspace, notes and connectors.</p>{authError && <p role="alert">{authError}</p>}<button onClick={() => identity?.signinRedirect()}>Sign in / Create account</button></main>;
  return <WorkspaceApp />;
}

function WorkspaceApp() {
  const [view, setView] = useState<View>("dashboard");
  const liveCount = useLiveMeetings();

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>Ops AI Agent</h1>
        <div className="sidebar-tag">AI for Work</div>
        {liveCount > 0 && (
          <div className="live-badge" title="A meeting is being transcribed live">
            <span className="live-dot" />
            {liveCount} live meeting{liveCount > 1 ? "s" : ""}
          </div>
        )}
        <nav>
          <NavBtn id="dashboard" active={view} onClick={setView}>
            Dashboard
          </NavBtn>
          <NavBtn id="upload" active={view} onClick={setView}>
            Upload transcript
          </NavBtn>
          <NavBtn id="tasks" active={view} onClick={setView}>
            Tasks
          </NavBtn>
          <NavBtn id="decisions" active={view} onClick={setView}>
            Decisions
          </NavBtn>
          <NavBtn id="intelligence" active={view} onClick={setView}>
            Intelligence
          </NavBtn>
          <NavBtn id="settings" active={view} onClick={setView}>Workspace & connectors</NavBtn>
        </nav>
        {authConfigured && <button onClick={() => identity?.signoutRedirect()}>Sign out</button>}
        {!authConfigured && <div className="api-key-control">
          <label htmlFor="console-api-key">Backend API key (if set)</label>
          <input
            id="console-api-key"
            type="password"
            autoComplete="off"
            placeholder="Optional"
            onChange={(event) => setApiKey(event.target.value)}
            aria-describedby="console-api-key-help"
          />
          <p id="console-api-key-help">Used for edits and uploads. Cleared when you reload.</p>
        </div>}
      </aside>
      <main className="main">
        {view === "settings" && <WorkspaceSettings />}
        {view === "dashboard" && <Dashboard />}
        {view === "upload" && <TranscriptUpload onIngested={() => setView("tasks")} />}
        {view === "tasks" && <Tasks />}
        {view === "decisions" && <Decisions />}
        {view === "intelligence" && <Intelligence />}
      </main>
    </div>
  );
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

function NavBtn({
  id,
  active,
  onClick,
  children,
}: {
  id: View;
  active: View;
  onClick: (v: View) => void;
  children: React.ReactNode;
}) {
  return (
    <button className={id === active ? "active" : ""} onClick={() => onClick(id)}>
      {children}
    </button>
  );
}
