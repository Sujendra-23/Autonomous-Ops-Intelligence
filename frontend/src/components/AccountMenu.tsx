import { useEffect, useRef, useState } from "react";
import { authConfigured } from "../auth";
import type { Navigate } from "../navigation";
import { Icon } from "./Icon";

export function AccountMenu({
  workspaceName,
  onNavigate,
  onLogout,
  busy,
  error,
}: {
  workspaceName: string;
  onNavigate: Navigate;
  onLogout: () => void;
  busy: boolean;
  error: string;
}) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        trigger.current?.focus();
      }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  return (
    <div
      className="account-control"
      ref={root}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget as Node | null))
          setOpen(false);
      }}
    >
      <button
        ref={trigger}
        className="account-trigger"
        aria-label="Account"
        aria-expanded={open}
        aria-controls="account-actions"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="account-avatar" aria-hidden="true">
          {workspaceName.charAt(0).toUpperCase()}
        </span>
        <span className="account-trigger-label">Account</span>
        <Icon name="down" />
      </button>
      {open && (
        <div
          className="account-panel"
          id="account-actions"
          role="region"
          aria-label="Account actions"
        >
          <strong>{workspaceName}</strong>
          <p>
            {authConfigured
              ? "Company workspace"
              : "Local workspace · no account session"}
          </p>
          <button
            onClick={() => {
              setOpen(false);
              onNavigate("settings");
            }}
          >
            <Icon name="settings" />
            Workspace settings
          </button>
          {authConfigured ? (
            <button
              onClick={() => {
                if (!busy) onLogout();
              }}
              aria-disabled={busy}
              aria-busy={busy}
            >
              <Icon name="logout" />
              {busy ? "Logging out…" : "Logout"}
            </button>
          ) : (
            <a href="/login">
              <Icon name="login" />
              Login
            </a>
          )}
          {error && (
            <p className="account-error" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
