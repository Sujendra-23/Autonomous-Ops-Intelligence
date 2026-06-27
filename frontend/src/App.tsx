import { useState } from "react";
import { Dashboard } from "./components/Dashboard";
import { TranscriptUpload } from "./components/TranscriptUpload";
import { Tasks } from "./components/Tasks";
import { Decisions } from "./components/Decisions";
import { Intelligence } from "./components/Intelligence";

type View = "dashboard" | "upload" | "tasks" | "decisions" | "intelligence";

export function App() {
  const [view, setView] = useState<View>("dashboard");

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>Ops AI Agent</h1>
        <div className="sidebar-tag">AI for Work</div>
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
