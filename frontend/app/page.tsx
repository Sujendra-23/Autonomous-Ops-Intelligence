import type { Metadata } from "next";
import { Dashboard } from "@/components/Dashboard";

export const metadata: Metadata = { title: { absolute: "Dashboard | Ops AI Agent" } };

export default function Page() {
  return <Dashboard />;
}
