// Receiver for Autonomous Operational Intelligence webhooks. Node 18+, no dependencies.
//
//   WEBHOOK_SECRET=<same value as the backend> node receiver.mjs [port]
//
// It verifies the HMAC signature over the exact raw request body, rejects stale timestamps,
// acknowledges duplicate event IDs without handling them twice, and returns 2xx quickly.
// Replace `handleEvent` with your own logic.

import { createHmac, timingSafeEqual } from "node:crypto";
import http from "node:http";
import { fileURLToPath } from "node:url";

export const TOLERANCE_SECONDS = 300;
const MAX_BODY_BYTES = 1_000_000;
const SEEN_LIMIT = 10_000;

export function sign(rawBody, timestamp, secret) {
  const digest = createHmac("sha256", secret)
    .update(Buffer.concat([Buffer.from(`${timestamp}.`), rawBody]))
    .digest("hex");
  return `sha256=${digest}`;
}

// True only for a fresh timestamp and a matching signature of the raw bytes.
export function verify(rawBody, timestamp, signature, secret, { now = Date.now() / 1000, tolerance = TOLERANCE_SECONDS } = {}) {
  if (!/^\d+$/.test(timestamp ?? "")) return false;
  if (Math.abs(now - Number(timestamp)) > tolerance) return false;
  const expected = Buffer.from(sign(rawBody, timestamp, secret));
  const received = Buffer.from(signature ?? "");
  return expected.length === received.length && timingSafeEqual(expected, received);
}

// Bounded in-memory dedupe. Use a database unique key on the event ID in production.
export class SeenEvents {
  #ids = new Set();
  constructor(limit = SEEN_LIMIT) {
    this.limit = limit;
  }
  has(id) {
    return this.#ids.has(id);
  }
  add(id) {
    this.#ids.add(id);
    if (this.#ids.size > this.limit) this.#ids.delete(this.#ids.values().next().value);
  }
}

export function handleEvent(event) {
  const { type, data = {} } = event;
  if (type === "task.created") console.log(`new task ${data.id}: ${data.title} (owner ${data.owner})`);
  else if (type === "task.updated") console.log(`task ${data.id} is now ${data.status}`);
  else if (type === "meeting.completed") console.log(`meeting '${data.title}' finished extraction`);
  else console.log(`ignoring unknown event type ${type}`);
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    let size = 0;
    req.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(Object.assign(new Error("too large"), { status: 413 }));
        req.destroy();
      } else chunks.push(chunk);
    });
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

export function createServer(secret, { seen = new SeenEvents(), handler = handleEvent } = {}) {
  const reply = (res, status, body) => {
    res.writeHead(status, { "Content-Type": "application/json" });
    res.end(JSON.stringify(body));
  };
  return http.createServer(async (req, res) => {
    if (req.method !== "POST") return reply(res, 405, { error: "POST only" });
    let raw;
    try {
      raw = await readBody(req); // verify these exact bytes, never re-serialized JSON
    } catch (err) {
      return reply(res, err.status ?? 400, { error: "bad body" });
    }
    if (!verify(raw, req.headers["x-aoi-timestamp"], req.headers["x-aoi-signature"], secret)) {
      return reply(res, 401, { error: "invalid signature or timestamp" });
    }
    let event, eventId;
    try {
      event = JSON.parse(raw.toString("utf8"));
      eventId = req.headers["x-aoi-event-id"] ?? event.id;
      if (!eventId) throw new Error("no event id");
    } catch {
      return reply(res, 400, { error: "malformed event" });
    }
    if (seen.has(eventId)) return reply(res, 200, { status: "duplicate" });
    try {
      await handler(event);
    } catch {
      // A 5xx makes the sender retry. Never echo internals back.
      return reply(res, 500, { error: "handler failed" });
    }
    seen.add(eventId);
    reply(res, 200, { status: "ok" });
  });
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const secret = process.env.WEBHOOK_SECRET ?? "";
  if (secret.length < 32) {
    console.error("Set WEBHOOK_SECRET to the backend's secret (at least 32 characters)");
    process.exit(1);
  }
  const port = Number(process.argv[2] ?? 8081);
  createServer(secret).listen(port, "127.0.0.1", () => console.log(`listening on http://127.0.0.1:${port}`));
}
