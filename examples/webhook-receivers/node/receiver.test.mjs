import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { createServer, SeenEvents, sign, verify } from "./receiver.mjs";

const vector = JSON.parse(readFileSync(new URL("../vector.json", import.meta.url), "utf8"));
const raw = Buffer.from(vector.raw_body);
const sent = Number(vector.timestamp);
const signature = vector.headers["X-AOI-Signature"];

test("matches the signature produced by the backend", () => {
  assert.equal(sign(raw, vector.timestamp, vector.secret), signature);
  assert.ok(verify(raw, vector.timestamp, signature, vector.secret, { now: sent + 5 }));
});

test("rejects tampered bodies, wrong secrets, stale and malformed timestamps", () => {
  const ok = (body, ts, sig, secret, now) => verify(body, ts, sig, secret, { now });
  assert.ok(!ok(Buffer.from(vector.raw_body.replace("high", "low")), vector.timestamp, signature, vector.secret, sent));
  assert.ok(!ok(raw, vector.timestamp, signature, "x".repeat(32), sent));
  assert.ok(!ok(raw, vector.timestamp, signature, vector.secret, sent + 301));
  assert.ok(!ok(raw, "not-a-number", signature, vector.secret, sent));
  assert.ok(!ok(raw, vector.timestamp, undefined, vector.secret, sent));
  assert.ok(!ok(raw, vector.timestamp, "sha256=", vector.secret, sent));
});

test("server acknowledges once, dedupes by event id, and rejects bad signatures", async () => {
  const handled = [];
  const server = createServer(vector.secret, { seen: new SeenEvents(), handler: (e) => handled.push(e.id) });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/`;
  // The fixed vector is older than the 300 s window, so sign a fresh copy of the same body.
  const timestamp = String(Math.floor(Date.now() / 1000));
  const headers = {
    "Content-Type": "application/json",
    "X-AOI-Event-ID": vector.headers["X-AOI-Event-ID"],
    "X-AOI-Timestamp": timestamp,
    "X-AOI-Signature": sign(raw, timestamp, vector.secret),
  };
  try {
    const first = await fetch(url, { method: "POST", headers, body: raw });
    assert.deepEqual([first.status, await first.json()], [200, { status: "ok" }]);
    const again = await fetch(url, { method: "POST", headers, body: raw });
    assert.deepEqual([again.status, await again.json()], [200, { status: "duplicate" }]);
    assert.equal(handled.length, 1);
    const bad = await fetch(url, { method: "POST", headers: { ...headers, "X-AOI-Signature": "sha256=00" }, body: raw });
    assert.equal(bad.status, 401);
  } finally {
    server.close();
  }
});
