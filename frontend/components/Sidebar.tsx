"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";
import { api, setApiKey } from "@/lib/api";
import { authConfigured, getIdentity } from "@/lib/auth";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/upload", label: "Upload transcript" },
  { href: "/tasks", label: "Tasks" },
  { href: "/decisions", label: "Decisions" },
  { href: "/intelligence", label: "Intelligence" },
  { href: "/settings", label: "Workspace & connectors" },
];

export function Sidebar() {
  const pathname = usePathname();
  const liveCount = useLiveMeetings();
  return (
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
        {LINKS.map(({ href, label }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link key={href} href={href} className={active ? "active" : ""} aria-current={active ? "page" : undefined}>
              {label}
            </Link>
          );
        })}
      </nav>
      {authConfigured && <button onClick={() => getIdentity()?.signoutRedirect()}>Sign out</button>}
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
