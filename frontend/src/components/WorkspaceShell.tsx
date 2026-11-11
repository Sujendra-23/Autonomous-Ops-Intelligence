import { useEffect, useRef, useState, type ReactNode } from "react";
import { request, setApiKey } from "../api";
import { authConfigured, signOut } from "../auth";
import type { Navigate, View } from "../navigation";
import { BrandMark, Icon, type IconName } from "./Icon";
import { VoiceAssistant } from "./VoiceAssistant";
import { WorkspaceMotion } from "./WorkspaceMotion";

const primaryNav: { id: View; label: string; icon: IconName }[] = [
  { id: "dashboard", label: "Dashboard", icon: "overview" },
  { id: "meetings", label: "Meetings", icon: "meetings" },
  { id: "tasks", label: "Tasks", icon: "tasks" },
  { id: "roadmaps", label: "Roadmaps", icon: "roadmap" },
  { id: "decisions", label: "Decisions", icon: "decisions" },
  { id: "intelligence", label: "Intelligence", icon: "intelligence" },
  { id: "weekly-summary", label: "Weekly summary", icon: "summary" },
];
const workspaceNav: { id: View; label: string; icon: IconName }[] = [
  { id: "settings", label: "Workspace & connectors", icon: "settings" },
  { id: "extension", label: "Recording apps", icon: "mic" },
  { id: "pricing", label: "Plans & pricing", icon: "book" },
];
const labels: Record<View, string> = {
  dashboard: "Overview", roadmaps: "Roadmaps", meetings: "Meetings", tasks: "Tasks", decisions: "Decisions",
  intelligence: "Intelligence", "weekly-summary": "Weekly summary", settings: "Settings",
  extension: "Recording apps", pricing: "Plans & pricing", upload: "Upload transcript",
};

export function WorkspaceShell({ view, liveCount, onNavigate, children }: {
  view: View; liveCount: number; onNavigate: Navigate; children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [signOutError, setSignOutError] = useState("");
  const [companyName, setCompanyName] = useState("Your workspace");
  useEffect(() => {
    if (!authConfigured) return;
    const controller = new AbortController();
    const update = () => void request<{ workspace_id: string; workspaces: { id: string; name: string }[] }>("/api/account/me", { signal: controller.signal })
      .then(account => { if (!controller.signal.aborted) setCompanyName(account.workspaces.find(workspace => workspace.id === account.workspace_id)?.name || "Your workspace"); })
      .catch(() => {});
    update();
    window.addEventListener("aoi-workspace-updated", update);
    return () => { controller.abort(); window.removeEventListener("aoi-workspace-updated", update); };
  }, []);
  const contentRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const sidebar = document.getElementById("workspace-navigation");
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    sidebar?.querySelector<HTMLButtonElement>("button")?.focus();
    if (contentRef.current) contentRef.current.inert = true;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
      if (event.key !== "Tab") return;
      const controls = [...(sidebar?.querySelectorAll<HTMLElement>("button, input, summary, a[href]") ?? [])].filter(element => element.getClientRects().length > 0);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      if (contentRef.current) contentRef.current.inert = false;
      previousFocus?.focus();
    };
  }, [menuOpen]);
  function navigate(next: View) { setMenuOpen(false); onNavigate(next); }
  const date = new Intl.DateTimeFormat(undefined, { weekday: "short", month: "short", day: "numeric" }).format(new Date());

  return <div className={`app studio-app${menuOpen ? " menu-open" : ""}`}>
    <a className="skip-link" href="#workspace-main">Skip to workspace</a>
    {menuOpen && <button className="sidebar-backdrop" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
    <aside className="sidebar" id="workspace-navigation" aria-label="Workspace navigation">
      <button className="drawer-close" aria-label="Close navigation drawer" onClick={() => setMenuOpen(false)}><Icon name="close"/></button>
      <button className="studio-brand" onClick={() => navigate("dashboard")} aria-label="AOI home"><span className="brand-icon"><BrandMark /></span><span>aoi<span className="brand-period">.</span></span><span className="brand-descriptor">meeting<br/>intelligence</span></button>
      <button className="workspace-switcher" onClick={() => navigate("settings")}><span className="workspace-avatar">{companyName.charAt(0).toUpperCase()}</span><span>{companyName}<small>{authConfigured ? "Company workspace" : "Local workspace"}</small></span><Icon name="down" /></button>
      <div className="nav-label">Workspace</div>
      <nav aria-label="Main navigation">{primaryNav.map(item => <button key={item.id} className={view === item.id ? "active" : ""} aria-current={view === item.id ? "page" : undefined} onClick={() => navigate(item.id)}><Icon name={item.icon}/><span>{item.label}</span>{item.id === "meetings" && liveCount > 0 && <span className="nav-live-count">{liveCount} live</span>}{view === item.id && <span className="nav-active-dot"/>}</button>)}</nav>
      <div className="nav-label tools-label">Make it yours</div>
      <nav aria-label="Workspace tools">{workspaceNav.map(item => <button key={item.id} className={view === item.id ? "active" : ""} aria-current={view === item.id ? "page" : undefined} onClick={() => navigate(item.id)}><Icon name={item.icon}/><span>{item.label}</span></button>)}</nav>
      <div className="sidebar-bottom">
        <div className="extension-invitation"><span className="invitation-signal" aria-hidden="true"><i/><i/><i/><i/><i/></span><p>Stay in the conversation.<br/><strong>We’ll catch the next steps.</strong></p><button onClick={() => navigate("extension")}>Set up recording<Icon name="arrow"/></button></div>
        {!authConfigured && <details className="api-key-control"><summary><Icon name="settings"/>Backend connection</summary><label htmlFor="console-api-key">Backend API key (if set)</label><input id="console-api-key" type="password" autoComplete="off" placeholder="Optional" onChange={event => setApiKey(event.target.value)} aria-describedby="console-api-key-help"/><p id="console-api-key-help">Used for edits and uploads. Cleared when you reload.</p></details>}
        {authConfigured && <><button className="sign-out" onClick={() => void signOut().catch(() => setSignOutError("Could not sign out. Check the connection and retry."))}>Sign out</button>{signOutError && <p className="sidebar-auth-error" role="alert">{signOutError}</p>}</>}
        <div className="sidebar-footnote"><span className="sidebar-presence-dot"/>Desktop + web<span>AOI</span></div>
      </div>
    </aside>
    <div className="studio-content" ref={contentRef}>
      <div className="workspace-topbar">
        <div className="topbar-location"><button className="mobile-menu icon-action" aria-label={menuOpen ? "Close navigation" : "Open navigation"} aria-expanded={menuOpen} aria-controls="workspace-navigation" onClick={() => setMenuOpen(value => !value)}><Icon name={menuOpen ? "close" : "menu"}/></button><span>Workspace</span><Icon name="chevron"/><strong>{labels[view]}</strong></div>
        <div className="topbar-actions"><time>{date}</time><button className="topbar-search" aria-label="Find an answer" onClick={() => navigate("intelligence")}><Icon name="search"/><span>Find an answer</span><Icon name="arrow"/></button><button className="topbar-upload primary" aria-label="New transcript" onClick={() => navigate("upload")}><Icon name="plus"/><span>New transcript</span></button><VoiceAssistant view={view} onNavigate={onNavigate}/><button className="topbar-avatar" aria-label="Open workspace settings" onClick={() => navigate("settings")}>{companyName.charAt(0).toUpperCase()}</button></div>
      </div>
      <main className="main" id="workspace-main" tabIndex={-1}>{children}</main>
      <WorkspaceMotion view={view}/>
      <footer className="workspace-footer"><span>A little more presence. A lot less admin.</span><span><span className="footer-mark"><BrandMark/></span>Built for what comes next.</span></footer>
    </div>
  </div>;
}
