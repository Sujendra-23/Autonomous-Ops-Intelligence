"use client";

import { useRouter } from "next/navigation";
import { TranscriptUpload } from "./TranscriptUpload";

export function UploadPage() {
  const router = useRouter();
  return <TranscriptUpload onIngested={() => router.push("/tasks")} />;
}
