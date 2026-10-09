import { anthropic } from "@ai-sdk/anthropic";
import { createChatHandler } from "@/lib/chat-handler";

export const maxDuration = 60;
export const dynamic = "force-dynamic";

// Built per request so the environment is read at runtime, not baked in at build time.
export async function POST(req: Request) {
  return createChatHandler({
    model: anthropic(process.env.ASK_MODEL || process.env.ANTHROPIC_MODEL || "claude-sonnet-5-5"),
    backendUrl: process.env.BACKEND_URL || "http://localhost:8000",
    intelligenceKey: process.env.INTELLIGENCE_API_KEY || "",
  })(req);
}
