import { createHmac, timingSafeEqual } from "node:crypto";

export interface Config {
  verificationToken?: string;
  notionWorkspaceId: string;
  notionIntegrationId: string;
  aoiWorkspaceId: string;
  aoiToken: string;
  aoiBaseUrl: string;
  // Explicit, one-time setup only. Store the token securely; never echo it in a response.
  onVerification?: (token: string) => Promise<void>;
}

const MAX_BODY = 128 * 1024;
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const canonical = (value: string) => value.replaceAll("-", "").toLowerCase();
const reply = (status: number) => new Response(null, { status });

export function createNotionHandler(config: Config, send: typeof fetch = fetch) {
  const base = new URL(config.aoiBaseUrl);
  if (base.username || base.password || base.search || base.hash || base.pathname !== "/" ||
      (base.protocol !== "https:" && !(base.protocol === "http:" &&
        ["localhost", "127.0.0.1", "backend"].includes(base.hostname)))) {
    throw new Error("AOI_BASE_URL must be an HTTPS origin (local development may use HTTP)");
  }
  if (!config.aoiToken || ![config.aoiWorkspaceId, config.notionWorkspaceId,
      config.notionIntegrationId].every(id => uuid.test(id))) {
    throw new Error("Configure a workspace-bound AOI token and valid workspace/integration IDs");
  }
  if (!config.verificationToken && !config.onVerification) {
    throw new Error("Configure a Notion verification token or explicit setup mode");
  }
  return async function handle(request: Request): Promise<Response> {
    if (new URL(request.url).pathname !== "/webhooks/notion") return reply(404);
    if (request.method !== "POST") return reply(405);
    const reader = request.body?.getReader();
    if (!reader) return reply(400);
    const chunks: Uint8Array[] = [];
    let length = 0;
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        length += chunk.value.byteLength;
        if (length > MAX_BODY) {
          await reader.cancel();
          return reply(413);
        }
        chunks.push(chunk.value);
      }
    } catch { return reply(400); }
    const raw = Buffer.concat(chunks);
    if (config.verificationToken) {
      const signature = request.headers.get("x-notion-signature") ?? "";
      if (!/^sha256=[a-f0-9]{64}$/.test(signature)) return reply(401);
      const expected = `sha256=${createHmac("sha256", config.verificationToken).update(raw).digest("hex")}`;
      if (!timingSafeEqual(Buffer.from(signature), Buffer.from(expected))) return reply(401);
    }
    let event: Record<string, unknown>;
    try {
      const parsed: unknown = JSON.parse(raw.toString("utf8"));
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return reply(400);
      event = parsed as Record<string, unknown>;
    } catch { return reply(400); }
    if (!config.verificationToken) {
      if (typeof event.verification_token !== "string" || event.verification_token.length < 16 ||
          event.verification_token.length > 512) return reply(400);
      try { await config.onVerification!(event.verification_token); }
      catch { return reply(503); }
      return reply(200);
    }
    if (typeof event.workspace_id !== "string" || typeof event.integration_id !== "string" ||
        canonical(event.workspace_id) !== canonical(config.notionWorkspaceId) ||
        canonical(event.integration_id) !== canonical(config.notionIntegrationId)) return reply(403);
    if (event.type !== "page.content_updated") return reply(204);
    const entity = event.entity as { id?: unknown; type?: unknown } | null;
    if (typeof event.id !== "string" || !uuid.test(event.id) || !entity ||
        entity.type !== "page" || typeof entity.id !== "string" || !uuid.test(entity.id)) return reply(400);
    try {
      const response = await send(new URL("/api/integrations/notion/reconcile", base), {
        method: "POST", redirect: "error", signal: AbortSignal.timeout(15_000),
        headers: {
          "Authorization": `Bearer ${config.aoiToken}`,
          "X-API-Key": config.aoiToken,
          "X-Workspace-ID": config.aoiWorkspaceId,
          "Content-Type": "application/json",
        },
        // Never accept an AOI task ID, role, workspace or status from the public payload.
        body: JSON.stringify({ page_id: entity.id }),
      });
      await response.body?.cancel();
      return reply(response.ok ? 204 : 503); // Let Notion retry failed AOI requests.
    } catch { return reply(503); }
  };
}
