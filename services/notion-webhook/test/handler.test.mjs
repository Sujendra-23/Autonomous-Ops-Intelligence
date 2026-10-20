import test from "node:test";
import assert from "node:assert/strict";
import { createHmac, randomUUID } from "node:crypto";
import { createNotionHandler } from "../dist/handler.js";

const config = {
  verificationToken: "secret-verification-token",
  notionWorkspaceId: randomUUID(), notionIntegrationId: randomUUID(),
  aoiWorkspaceId: randomUUID(), aoiToken: "aoi_workspace-bound-token",
  aoiBaseUrl: "https://aoi.example",
};
const event = () => ({ id: randomUUID(), type: "page.content_updated",
  workspace_id: config.notionWorkspaceId, integration_id: config.notionIntegrationId,
  entity: { id: randomUUID(), type: "page" } });
const request = (body, options = {}) => {
  const raw = typeof body === "string" ? body : JSON.stringify(body);
  return new Request("https://hooks.example/webhooks/notion", { method: "POST", body: raw,
    headers: { "X-Notion-Signature": `sha256=${createHmac("sha256", config.verificationToken)
      .update(raw).digest("hex")}`, ...options.headers } });
};

test("verifies exact bytes and forwards only the page to the configured AOI workspace", async () => {
  const body = event(); let called = 0;
  const handler = createNotionHandler(config, async (url, init) => {
    called++;
    assert.equal(String(url), "https://aoi.example/api/integrations/notion/reconcile");
    assert.equal(init.headers.Authorization, `Bearer ${config.aoiToken}`);
    assert.equal(init.headers["X-API-Key"], config.aoiToken);
    assert.equal(init.headers["X-Workspace-ID"], config.aoiWorkspaceId);
    assert.deepEqual(JSON.parse(init.body), { page_id: body.entity.id });
    assert.equal(init.redirect, "error");
    return new Response(null, { status: 200 });
  });
  // Duplicate and old events fetch current provider state; no status is applied from a payload.
  for (let i = 0; i < 2; i++) {
    assert.equal((await handler(request(JSON.stringify(body, null, 2)))).status, 204);
  }
  assert.equal(called, 2);
});

for (const signature of ["", "sha256=bad", "sha256=" + "0".repeat(64)]) {
  test(`rejects invalid signature ${signature.slice(0, 15)}`, async () => {
    const handler = createNotionHandler(config, () => assert.fail("must not forward"));
    assert.equal((await handler(request(event(), { headers: { "X-Notion-Signature": signature } }))).status, 401);
  });
}

test("tampered bytes are rejected", async () => {
  const original = request(event());
  const changed = new Request(original.url, {method: "POST", body: "{}", headers: original.headers});
  assert.equal((await createNotionHandler(config)(changed)).status, 401);
});

for (const field of ["workspace_id", "integration_id"]) {
  test(`rejects another ${field}`, async () => {
    const handler = createNotionHandler(config, () => assert.fail("must not forward"));
    assert.equal((await handler(request({ ...event(), [field]: randomUUID() }))).status, 403);
  });
}

for (const body of ["null", "[]", "invalid", JSON.stringify({ ...event(), entity: null }),
  JSON.stringify({ ...event(), entity: { type: "page", id: "../../api/account" } })]) {
  test(`rejects malformed payload ${body.slice(0, 20)}`, async () => {
    assert.equal((await createNotionHandler(config)(request(body))).status, 400);
  });
}

test("bounds the body, including without Content-Length", async () => {
  assert.equal((await createNotionHandler(config)(request("a".repeat(128 * 1024 + 1)))).status, 413);
});

test("ignores other signed event types", async () => {
  const handler = createNotionHandler(config, () => assert.fail("must not forward"));
  assert.equal((await handler(request({ ...event(), type: "comment.created" }))).status, 204);
});

test("returns retriable failure on backend errors and timeouts", async () => {
  for (const send of [async () => new Response(null, { status: 403 }), async () => { throw Error("timeout"); }]) {
    assert.equal((await createNotionHandler(config, send)(request(event()))).status, 503);
  }
});

test("setup callback receives token without echoing or forwarding it", async () => {
  let saved;
  const handler = createNotionHandler({ ...config, verificationToken: undefined,
    onVerification: async token => { saved = token; } }, () => assert.fail("must not forward"));
  const response = await handler(request({verification_token: "one-time-setup-token"}));
  assert.equal(response.status, 200);
  assert.equal(saved, "one-time-setup-token");
  assert.equal(await response.text(), "");
});

test("active mode rejects unsigned setup attempts", async () => {
  assert.equal((await createNotionHandler(config)(request({ verification_token: "attacker-token" },
    { headers: { "X-Notion-Signature": "" } }))).status, 401);
});

test("requires explicit configuration and prevents redirects/credential-bearing AOI URLs", () => {
  for (const aoiBaseUrl of ["http://untrusted.example", "https://aoi.example/path", "https://user:pass@aoi.example"]) {
    assert.throws(() => createNotionHandler({ ...config, aoiBaseUrl }));
  }
  assert.throws(() => createNotionHandler({ ...config, verificationToken: undefined }));
});

test("checks method and route", async () => {
  const handler = createNotionHandler(config);
  assert.equal((await handler(new Request("https://hooks.example/other"))).status, 404);
  assert.equal((await handler(new Request("https://hooks.example/webhooks/notion"))).status, 405);
});
