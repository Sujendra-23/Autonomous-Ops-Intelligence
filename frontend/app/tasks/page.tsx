import type { Metadata } from "next";
import { Tasks } from "@/components/Tasks";

export const metadata: Metadata = { title: "Tasks" };

export default function Page() {
  return <Tasks />;
}
