import { createServer } from "node:http";
import { writeFile } from "node:fs/promises";
import { Readable } from "node:stream";
import { createNotionHandler } from "./handler.js";

const setupFile = process.env.NOTION_WEBHOOK_SETUP_FILE;
const handler = createNotionHandler({
  verificationToken: process.env.NOTION_WEBHOOK_VERIFICATION_TOKEN,
  notionWorkspaceId: process.env.NOTION_WORKSPACE_ID ?? "",
  notionIntegrationId: process.env.NOTION_INTEGRATION_ID ?? "",
  aoiWorkspaceId: process.env.AOI_WORKSPACE_ID ?? "",
  aoiToken: process.env.AOI_TOKEN ?? "",
  aoiBaseUrl: process.env.AOI_BASE_URL ?? "http://localhost:8000",
  onVerification: setupFile ? token => writeFile(setupFile, token, { mode: 0o600, flag: "wx" }) : undefined,
});

const server = createServer(async (req, res) => {
  try {
    const headers = new Headers();
    for (const [key, value] of Object.entries(req.headers)) {
      if (value !== undefined) headers.set(key, Array.isArray(value) ? value.join(",") : value);
    }
    const request = new Request(new URL(req.url ?? "/", "http://localhost"), {
      method: req.method, headers,
      ...(req.method !== "GET" && req.method !== "HEAD" ? {
        body: Readable.toWeb(req) as ReadableStream<Uint8Array>, duplex: "half",
      } : {}),
    });
    const response = await handler(request);
    res.writeHead(response.status);
    res.end();
  } catch { res.writeHead(500); res.end(); }
});
server.requestTimeout = 20_000;
server.headersTimeout = 10_000;
server.listen(Number(process.env.PORT ?? 8787), "0.0.0.0");
