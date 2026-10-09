import type { Metadata } from "next";
import { Intelligence } from "@/components/Intelligence";

export const metadata: Metadata = { title: "Intelligence" };

export default function Page() {
  return <Intelligence />;
}
