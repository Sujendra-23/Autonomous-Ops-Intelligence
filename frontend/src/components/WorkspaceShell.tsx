import { useEffect, useRef, useState, type ReactNode } from "react";
import { request, setApiKey } from "../api";
import { authConfigured, signOut } from "../auth";
import type { Navigate, View } from "../navigation";
import { BrandMark, Icon, type IconName } from "./Icon";
import { VoiceAssistant } from "./VoiceAssistant";
import { WorkspaceMotion } from "./WorkspaceMotion";
import { AccountMenu } from "./AccountMenu";

const primaryNav: { id: View; label: string; icon: IconName }[] = [
  { id: "dashboard", label: "Home", icon: "overview" },
  { id: "meetings", label: "Meetings", icon: "meetings" },
  { id: "tasks", label: "Tasks", icon: "tasks" },
  { id: "roadmaps", label: "Roadmaps", icon: "roadmap" },
  { id: "extension", label: "Recording apps", icon: "mic" },
];
const reportNav: { id: View; label: string; icon: IconName }[] = [
  { id: "decisions", label: "Decisions", icon: "decisions" },
  { id: "intelligence", label: "Search & insights", icon: "intelligence" },
  { id: "weekly-summary", label: "Weekly summary", icon: "summary" },
];
const labels: Record<View, string> = {
  dashboard: "Home",
  roadmaps: "Roadmaps",
  meetings: "Meetings",
  tasks: "Tasks",
  decisions: "Decisions",
  intelligence: "Intelligence",
  "weekly-summary": "Weekly summary",
  settings: "Settings",
  extension: "Recording apps",
  pricing: "Plans & pricing",
  upload: "Upload transcript",
};

export function WorkspaceShell({
  view,
  liveCount,
  onNavigate,
  children,
}: {
  view: View;
  liveCount: number;
  onNavigate: Navigate;
  children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [reportsOpen, setReportsOpen] = useState(() =>
    reportNav.some((item) => item.id === view),
  );
  useEffect(() => {
    if (reportNav.some((item) => item.id === view)) setReportsOpen(true);
  }, [view]);
  const [signOutError, setSignOutError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  async function logout() {
    if (loggingOut) return;
    setLoggingOut(true);
    setSignOutError("");
    try {
      await signOut();
    } catch {
      setSignOutError("Could not log out. Check the connection and retry.");
    } finally {
      setLoggingOut(false);
    }
  }
  const [companyName, setCompanyName] = useState("Your workspace");
  useEffect(() => {
    if (!authConfigured) return;
    const controller = new AbortController();
    const update = () =>
      void request<{
        workspace_id: string;
        workspaces: { id: string; name: string }[];
      }>("/api/account/me", { signal: controller.signal })
        .then((account) => {
          if (!controller.signal.aborted)
            setCompanyName(
              account.workspaces.find(
                (workspace) => workspace.id === account.workspace_id,
              )?.name || "Your workspace",
            );
        })
        .catch(() => {});
    update();
    window.addEventListener("aoi-workspace-updated", update);
    return () => {
      controller.abort();
      window.removeEventListener("aoi-workspace-updated", update);
    };
  }, []);
  const contentRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!menuOpen) return;
    const sidebar = document.getElementById("workspace-navigation");
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    sidebar?.querySelector<HTMLButtonElement>("button")?.focus();
    if (contentRef.current) contentRef.current.inert = true;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
      if (event.key !== "Tab") return;
      const controls = [
        ...(sidebar?.querySelectorAll<HTMLElement>(
          "button, input, summary, a[href]",
        ) ?? []),
      ].filter((element) => element.getClientRects().length > 0);
      const first = controls[0];
      const last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      }
      if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      if (contentRef.current) contentRef.current.inert = false;
      previousFocus?.focus();
    };
  }, [menuOpen]);
  function navigate(next: View) {
    setMenuOpen(false);
    onNavigate(next);
  }

  return (
    <div
      className={`app studio-app simplified-app${menuOpen ? " menu-open" : ""}`}
    >
      <a className="skip-link" href="#workspace-main">
        Skip to workspace
      </a>
      {menuOpen && (
        <button
          className="sidebar-backdrop"
          aria-label="Close navigation"
          onClick={() => setMenuOpen(false)}
        />
      )}
      <aside
        className="sidebar"
        id="workspace-navigation"
        aria-label="Workspace navigation"
      >
        <button
          className="drawer-close"
          aria-label="Close navigation drawer"
          onClick={() => setMenuOpen(false)}
        >
          <Icon name="close" />
        </button>
        <button
          className="studio-brand"
          onClick={() => navigate("dashboard")}
          aria-label="AOI home"
        >
          <span className="brand-icon">
            <BrandMark />
          </span>
          <span>
            aoi<span className="brand-period">.</span>
          </span>
        </button>
        <div className="workspace-identity">
          <span className="workspace-avatar">
            {companyName.charAt(0).toUpperCase()}
          </span>
          <span>
            {companyName}
            <small>
              {authConfigured ? "Company workspace" : "Local workspace"}
            </small>
          </span>
        </div>
        <nav aria-label="Main navigation">
          {primaryNav.map((item) => (
            <button
              key={item.id}
              className={view === item.id ? "active" : ""}
              aria-current={view === item.id ? "page" : undefined}
              onClick={() => navigate(item.id)}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
              {item.id === "meetings" && liveCount > 0 && (
                <span className="nav-live-count">{liveCount} live</span>
              )}
              {view === item.id && <span className="nav-active-dot" />}
            </button>
          ))}
        </nav>
        <details
          className="sidebar-reports"
          open={reportsOpen}
          onToggle={(event) => setReportsOpen(event.currentTarget.open)}
        >
          <summary
            className={
              reportNav.some((item) => item.id === view)
                ? "has-active-report"
                : ""
            }
          >
            <Icon name="summary" />
            <span>Reports</span>
            <Icon name="down" />
          </summary>
          <nav aria-label="Reports">
            {reportNav.map((item) => (
              <button
                key={item.id}
                className={view === item.id ? "active" : ""}
                aria-current={view === item.id ? "page" : undefined}
                onClick={() => navigate(item.id)}
              >
                <Icon name={item.icon} />
                <span>{item.label}</span>
              </button>
            ))}
          </nav>
        </details>
        <div className="sidebar-bottom">
          <button
            className={
              view === "settings"
                ? "sidebar-settings active"
                : "sidebar-settings"
            }
            onClick={() => navigate("settings")}
          >
            <Icon name="settings" />
            Settings
          </button>
          {!authConfigured && (
            <details className="api-key-control">
              <summary>
                <Icon name="settings" />
                Backend connection
              </summary>
              <label htmlFor="console-api-key">Backend API key (if set)</label>
              <input
                id="console-api-key"
                type="password"
                autoComplete="off"
                placeholder="Optional"
                onChange={(event) => setApiKey(event.target.value)}
                aria-describedby="console-api-key-help"
              />
              <p id="console-api-key-help">
                Used for edits and uploads. Cleared when you reload.
              </p>
            </details>
          )}
          {authConfigured && (
            <>
              <button
                className="sign-out"
                disabled={loggingOut}
                onClick={() => void logout()}
              >
                <Icon name="logout" />
                {loggingOut ? "Logging out…" : "Logout"}
              </button>
              {signOutError && (
                <p className="sidebar-auth-error" role="alert">
                  {signOutError}
                </p>
              )}
            </>
          )}
          {!authConfigured && (
            <a className="sidebar-session-link" href="/login">
              <Icon name="login" />
              Login
            </a>
          )}
        </div>
      </aside>
      <div className="studio-content" ref={contentRef}>
        <div className="workspace-topbar">
          <div className="workspace-scroll-progress" aria-hidden="true">
            <i />
          </div>
          <div className="topbar-location">
            <button
              className="mobile-menu icon-action"
              aria-label={menuOpen ? "Close navigation" : "Open navigation"}
              aria-expanded={menuOpen}
              aria-controls="workspace-navigation"
              onClick={() => setMenuOpen((value) => !value)}
            >
              <Icon name={menuOpen ? "close" : "menu"} />
            </button>
            <strong>{labels[view]}</strong>
          </div>
          <div className="topbar-actions">
            <button
              className="topbar-search"
              aria-label="Search"
              onClick={() => navigate("intelligence")}
            >
              <Icon name="search" />
              <span>Search</span>
            </button>
            <VoiceAssistant view={view} onNavigate={onNavigate} />
            {authConfigured ? <button className="header-session-action" onClick={() => void logout()} disabled={loggingOut}>
              <Icon name="logout" />{loggingOut ? "Logging out…" : "Logout"}
            </button> : <a className="header-session-action" href="/login"><Icon name="login" />Login</a>}
            <AccountMenu
              workspaceName={companyName}
              onNavigate={navigate}
              onLogout={() => void logout()}
              busy={loggingOut}
              error={signOutError}
            />
          </div>
        </div>
        {signOutError && <p className="header-session-error" role="alert">{signOutError}</p>}
        <main className="main" id="workspace-main" tabIndex={-1}>
          {children}
        </main>
        <WorkspaceMotion view={view} />
      </div>
    </div>
  );
}
