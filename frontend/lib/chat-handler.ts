import { convertToModelMessages, stepCountIs, streamText, tool, type LanguageModel, type UIMessage } from "ai";
import { z } from "zod";

// Server-only. This is the console's "trusted gateway" for natural-language questions:
//  - the Anthropic key and the analytics key (INTELLIGENCE_API_KEY) stay on the server, never in the browser
//  - the caller must already be authenticated to the backend (their own credentials are checked first)
//  - rows returned by the analytics endpoint are shown to the user but NOT shown to the model, matching the
//    backend's rule that database results are never sent to Claude (docs/analytics.md)

export type ChatDeps = {
  model: LanguageModel;
  backendUrl: string;
  intelligenceKey: string;
  fetchImpl?: typeof fetch;
};

export const MAX_MESSAGES = 20;
export const MAX_QUESTION_CHARS = 2000;
const FORWARDED = ["authorization", "x-api-key", "x-workspace-id"] as const;

const SYSTEM = `You are the ask assistant inside the Ops AI Agent console.
- For counts, lists and rollups about tasks, risks and blockers (for example "how many tasks are overdue"), call askAnalytics.
- For questions about what was said or decided in meetings, call searchTranscripts and answer only from what it returns, naming the meeting titles you used.
- After askAnalytics the table is already on screen. Do not repeat or guess its rows. Say in one sentence what you ran.
- If a tool returns an error, say so plainly. Never invent numbers or quotes.`;

type Json = Record<string, unknown>;

function forwardedHeaders(req: Request): Record<string, string> {
  const out: Record<string, string> = {};
  for (const name of FORWARDED) {
    const value = req.headers.get(name);
    if (value) out[name] = value;
  }
  return out;
}

export function buildTools(deps: ChatDeps, callerHeaders: Record<string, string>) {
  const doFetch = deps.fetchImpl ?? fetch;
  const post = (path: string, headers: Record<string, string>, body: Json) =>
    doFetch(`${deps.backendUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...headers },
      body: JSON.stringify(body),
      cache: "no-store",
    });

  return {
    askAnalytics: tool({
      description:
        "Answer a counting or listing question about tasks, risks or blockers by running a validated read-only SQL query. Returns a table that is displayed to the user.",
      inputSchema: z.object({ question: z.string().min(1).max(MAX_QUESTION_CHARS) }),
      execute: async ({ question }) => {
        if (!deps.intelligenceKey) return { error: "Analytics is not configured on this deployment." };
        const res = await post("/api/intelligence/ask", { "X-API-Key": deps.intelligenceKey }, { question });
        if (res.status === 422) return { error: "That question could not be answered with a safe query. Try rephrasing it." };
        if (!res.ok) return { error: `Analytics is unavailable (HTTP ${res.status}).` };
        return (await res.json()) as { columns: string[]; rows: unknown[][]; truncated: boolean; owners_masked: boolean };
      },
      // The model sees only the shape of the result, never the rows.
      toModelOutput: ({ output }) => {
        const out = output as { error?: string; columns?: string[]; rows?: unknown[][] };
        return {
          type: "text",
          value: out.error
            ? `Error: ${out.error}`
            : `The query returned ${out.rows?.length ?? 0} rows with columns ${(out.columns ?? []).join(", ")}. The rows are displayed to the user and are intentionally not shared with you.`,
        };
      },
    }),
    searchTranscripts: tool({
      description: "Semantic search over meeting transcript chunks. Use for what was said or decided.",
      inputSchema: z.object({ query: z.string().min(1).max(MAX_QUESTION_CHARS) }),
      execute: async ({ query }) => {
        const res = await post("/api/intelligence/search", callerHeaders, { query, limit: 5 });
        if (!res.ok) return { error: `Search is unavailable (HTTP ${res.status}).` };
        const hits = (await res.json()) as { transcript_title: string; content: string; score: number }[];
        return { hits: hits.map((h) => ({ title: h.transcript_title, score: h.score, content: h.content.slice(0, 800) })) };
      },
    }),
  };
}

function lastUserText(messages: UIMessage[]): string {
  const last = [...messages].reverse().find((m) => m.role === "user");
  return (last?.parts ?? []).map((p) => (p.type === "text" ? p.text : "")).join("");
}

export function createChatHandler(deps: ChatDeps) {
  const doFetch = deps.fetchImpl ?? fetch;
  return async function handler(req: Request): Promise<Response> {
    const callerHeaders = forwardedHeaders(req);

    // 1. The caller must be authenticated to the backend with their own credentials.
    let authCheck: Response;
    try {
      authCheck = await doFetch(`${deps.backendUrl}/api/intelligence/dashboard`, { headers: callerHeaders, cache: "no-store" });
    } catch {
      return Response.json({ error: "Backend unreachable" }, { status: 502 });
    }
    if (authCheck.status === 401 || authCheck.status === 403) return Response.json({ error: "Not signed in" }, { status: 401 });
    if (!authCheck.ok) return Response.json({ error: "Backend unavailable" }, { status: 502 });

    // 2. Validate the payload before spending model tokens.
    let messages: UIMessage[];
    try {
      ({ messages } = (await req.json()) as { messages: UIMessage[] });
    } catch {
      return Response.json({ error: "Invalid JSON" }, { status: 400 });
    }
    if (!Array.isArray(messages) || messages.length === 0 || messages.length > MAX_MESSAGES) {
      return Response.json({ error: `Send between 1 and ${MAX_MESSAGES} messages` }, { status: 400 });
    }
    if (lastUserText(messages).length > MAX_QUESTION_CHARS) {
      return Response.json({ error: `Questions are limited to ${MAX_QUESTION_CHARS} characters` }, { status: 413 });
    }

    const result = streamText({
      model: deps.model,
      system: SYSTEM,
      messages: await convertToModelMessages(messages),
      tools: buildTools(deps, callerHeaders),
      stopWhen: stepCountIs(4),
    });
    return result.toUIMessageStreamResponse();
  };
}
