import { describe, expect, it, vi } from "vitest";
import { MockLanguageModelV3, simulateReadableStream } from "ai/test";
import { createChatHandler, MAX_QUESTION_CHARS } from "@/lib/chat-handler";

const SENTINEL = "ROW-VALUE-THE-MODEL-MUST-NOT-SEE";
const usage = {
  inputTokens: { total: 1, noCache: 1, cacheRead: undefined, cacheWrite: undefined },
  outputTokens: { total: 1, text: 1, reasoning: undefined },
};
const finish = (unified: "stop" | "tool-calls") => ({ type: "finish", finishReason: { unified, raw: undefined }, usage });

/** Scripted model: first call asks for a tool, second call writes a sentence. Records every prompt it receives. */
function scriptedModel(toolName: string, input: object) {
  const prompts: unknown[] = [];
  const model = new MockLanguageModelV3({
    doStream: async (options) => {
      prompts.push(options.prompt);
      const first = prompts.length === 1;
      const chunks = first
        ? [
            { type: "stream-start", warnings: [] },
            { type: "tool-call", toolCallId: "call-1", toolName, input: JSON.stringify(input) },
            finish("tool-calls"),
          ]
        : [
            { type: "stream-start", warnings: [] },
            { type: "text-start", id: "t1" },
            { type: "text-delta", id: "t1", delta: "Ran the query." },
            { type: "text-end", id: "t1" },
            finish("stop"),
          ];
      return { stream: simulateReadableStream({ chunks: chunks as never[] }) };
    },
  });
  return { model, prompts };
}

type Call = { url: string; headers: Record<string, string>; body?: unknown };

function backend(handlers: Record<string, () => Response>) {
  const calls: Call[] = [];
  const fetchImpl = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    const u = String(url).replace("http://backend", "");
    calls.push({ url: u, headers: (init?.headers ?? {}) as Record<string, string>, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    const h = handlers[u];
    return h ? h() : new Response("not found", { status: 404 });
  }) as unknown as typeof fetch;
  return { fetchImpl, calls };
}

const ok = (obj: unknown) => () => Response.json(obj);
const question = (text: string) => ({ messages: [{ id: "m1", role: "user", parts: [{ type: "text", text }] }] });
const request = (body: unknown, headers: Record<string, string> = {}) =>
  new Request("http://console/api/console/chat", { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });

describe("chat handler", () => {
  it("rejects callers the backend does not authenticate, without invoking the model", async () => {
    const { model } = scriptedModel("askAnalytics", { question: "x" });
    const { fetchImpl } = backend({ "/api/intelligence/dashboard": () => new Response("no", { status: 401 }) });
    const res = await createChatHandler({ model, backendUrl: "http://backend", intelligenceKey: "K", fetchImpl })(request(question("hi"), { "x-api-key": "bad" }));
    expect(res.status).toBe(401);
    expect(model.doStreamCalls).toHaveLength(0);
  });

  it("validates the payload before spending model tokens", async () => {
    const { model } = scriptedModel("askAnalytics", { question: "x" });
    const { fetchImpl } = backend({ "/api/intelligence/dashboard": ok({}) });
    const handler = createChatHandler({ model, backendUrl: "http://backend", intelligenceKey: "K", fetchImpl });
    expect((await handler(request({ messages: [] }))).status).toBe(400);
    expect((await handler(request(question("x".repeat(MAX_QUESTION_CHARS + 1))))).status).toBe(413);
    expect(model.doStreamCalls).toHaveLength(0);
  });

  it("runs analytics with the server-held key, shows rows to the user, and keeps them from the model", async () => {
    const { model, prompts } = scriptedModel("askAnalytics", { question: "how many overdue tasks" });
    const { fetchImpl, calls } = backend({
      "/api/intelligence/dashboard": ok({}),
      "/api/intelligence/ask": ok({ columns: ["priority", "n"], rows: [[SENTINEL, 3]], truncated: false, owners_masked: true }),
    });
    const res = await createChatHandler({ model, backendUrl: "http://backend", intelligenceKey: "SERVER-KEY", fetchImpl })(
      request(question("how many overdue tasks"), { "x-api-key": "console-key" }),
    );
    expect(res.status).toBe(200);
    const stream = await res.text();

    const ask = calls.find((c) => c.url === "/api/intelligence/ask")!;
    expect(ask.headers["X-API-Key"]).toBe("SERVER-KEY"); // not the caller's key
    expect(ask.body).toEqual({ question: "how many overdue tasks" });

    expect(stream).toContain(SENTINEL); // the UI stream carries the table
    expect(stream).toContain("Ran the query.");
    expect(JSON.stringify(prompts[1])).toContain("intentionally not shared"); // the model got only the shape
    expect(JSON.stringify(prompts[1])).not.toContain(SENTINEL);
  });

  it("searches transcripts as the caller and returns the passages to the model", async () => {
    const { model, prompts } = scriptedModel("searchTranscripts", { query: "migration decision" });
    const { fetchImpl, calls } = backend({
      "/api/intelligence/dashboard": ok({}),
      "/api/intelligence/search": ok([{ chunk_id: "c", transcript_id: "t", transcript_title: "Weekly sync", content: "We chose a staged rollout.", score: 0.9 }]),
    });
    const res = await createChatHandler({ model, backendUrl: "http://backend", intelligenceKey: "SERVER-KEY", fetchImpl })(
      request(question("what did we decide about the migration"), { authorization: "Bearer tok", "x-workspace-id": "w1" }),
    );
    await res.text();
    const search = calls.find((c) => c.url === "/api/intelligence/search")!;
    expect(search.headers).toMatchObject({ authorization: "Bearer tok", "x-workspace-id": "w1" });
    expect(search.headers["X-API-Key"]).toBeUndefined(); // the analytics key is never used for search
    expect(JSON.stringify(prompts[1])).toContain("staged rollout");
  });

  it("reports a missing analytics key instead of calling the backend", async () => {
    const { model } = scriptedModel("askAnalytics", { question: "q" });
    const { fetchImpl, calls } = backend({ "/api/intelligence/dashboard": ok({}) });
    const res = await createChatHandler({ model, backendUrl: "http://backend", intelligenceKey: "", fetchImpl })(request(question("q")));
    expect(await res.text()).toContain("Analytics is not configured");
    expect(calls.some((c) => c.url === "/api/intelligence/ask")).toBe(false);
  });

  it("turns an unsafe-query rejection into a friendly message", async () => {
    const { model } = scriptedModel("askAnalytics", { question: "drop table" });
    const { fetchImpl } = backend({
      "/api/intelligence/dashboard": ok({}),
      "/api/intelligence/ask": () => new Response("{}", { status: 422 }),
    });
    const res = await createChatHandler({ model, backendUrl: "http://backend", intelligenceKey: "K", fetchImpl })(request(question("drop table")));
    expect(await res.text()).toContain("could not be answered with a safe query");
  });
});
