import type { Metadata } from "next";
import { headers } from "next/headers";
import { AuthGate } from "@/components/AuthGate";
import { Sidebar } from "@/components/Sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "Autonomous Operational Intelligence", template: "%s | Ops AI Agent" },
  description: "Console for the meeting-to-tasks agent: dashboard, tasks, decisions, drift detection and search.",
  robots: { index: false, follow: false }, // a private workspace console, not a public site
};

// Server component: renders the document shell. Data fetching stays in client components because the
// OIDC access token lives in the browser's sessionStorage, not in a cookie the server could read.
export default async function RootLayout({ children }: { children: React.ReactNode }) {
  // Reading request headers opts every page into per-request rendering, which is what lets Next stamp
  // the CSP nonce (set in middleware.ts) onto its inline scripts. Static prerendering would break hydration.
  await headers();
  return (
    <html lang="en">
      <body>
        <AuthGate>
          <div className="app">
            <Sidebar />
            <main className="main">{children}</main>
          </div>
        </AuthGate>
      </body>
    </html>
  );
}
