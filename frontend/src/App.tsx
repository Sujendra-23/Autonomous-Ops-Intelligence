import { useEffect, useState } from "react";
import { api } from "./api";
import { Dashboard } from "./components/Dashboard";
import { TranscriptUpload } from "./components/TranscriptUpload";
import { Tasks } from "./components/Tasks";
import { Decisions } from "./components/Decisions";
import { Intelligence } from "./components/Intelligence";

type View = "dashboard" | "upload" | "tasks" | "decisions" | "intelligence";

export function App() {
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
        </nav>
      </aside>
      <main className="main">
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
