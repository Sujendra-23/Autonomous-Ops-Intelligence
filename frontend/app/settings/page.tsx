import type { Metadata } from "next";
import { WorkspaceSettings } from "@/components/WorkspaceSettings";

export const metadata: Metadata = { title: "Workspace and connectors" };

export default function Page() {
  return <WorkspaceSettings />;
}
