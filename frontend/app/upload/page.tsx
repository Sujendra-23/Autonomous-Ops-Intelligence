import type { Metadata } from "next";
import { UploadPage } from "@/components/UploadPage";

export const metadata: Metadata = { title: "Upload transcript" };

export default function Page() {
  return <UploadPage />;
}
