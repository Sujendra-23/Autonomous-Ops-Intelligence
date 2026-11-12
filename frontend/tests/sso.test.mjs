// Exercises the real oidc-client-ts redirect/PKCE flow with a synthetic OIDC provider.
// No external identity account, credentials or live backend writes are used.
import { chromium } from "playwright";
import { createHash, generateKeyPairSync, sign } from "node:crypto";
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const origin = "http://127.0.0.1:5194";
const issuer = "https://sso.example.test";
const shots = path.join(root, "../.impeccable/review");
await mkdir(shots, { recursive: true });
const server = spawn(process.execPath, [path.join(root, "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5194", "--strictPort"], {
  cwd: root, env: { ...process.env, VITE_API_URL: "", VITE_AUTH_PROVIDER: "oidc", VITE_AUTH_AUTHORITY: issuer, VITE_AUTH_CLIENT_ID: "aoi-fixture", VITE_AUTH_PROVIDER_NAME: "Example company", VITE_AUTH_SCOPE: "openid profile email api.read", VITE_AUTH_AUDIENCE: "ops-api" }, stdio: "pipe",
});
const browser = await chromium.launch({ headless: true });
const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
function jwt(claims) {
  const part = value => Buffer.from(JSON.stringify(value)).toString("base64url");
  const payload = `${part({ alg: "RS256", kid: "fixture" })}.${part(claims)}`;
  return `${payload}.${sign("RSA-SHA256", Buffer.from(payload), privateKey).toString("base64url")}`;
}
try {
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error("Isolated SSO Vite exited");
    try { if ((await fetch(origin)).ok) break; } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  for (const [name, width, height] of [["desktop", 1440, 1000], ["mobile", 390, 844]]) {
    for (const endSession of [true, false]) {
      const context = await browser.newContext({ viewport: { width, height }, reducedMotion: "reduce" });
      const page = await context.newPage();
      page.setDefaultTimeout(10000);
      let auth, exchanges = 0, logouts = 0, bearer = "";
      const errors = [];
      page.on("pageerror", error => errors.push(error.message));
      const reply = (route, value) => route.fulfill({ contentType: "application/json", headers: { "Access-Control-Allow-Origin": origin }, body: JSON.stringify(value) });
      await context.route(`${issuer}/**`, route => {
        const url = new URL(route.request().url());
        if (route.request().method() === "OPTIONS") return route.fulfill({ status: 204, headers: { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "content-type,authorization" } });
        if (url.pathname.endsWith("openid-configuration")) return reply(route, { issuer, authorization_endpoint: `${issuer}/authorize`, token_endpoint: `${issuer}/token`, userinfo_endpoint: `${issuer}/userinfo`, ...(endSession ? { end_session_endpoint: `${issuer}/logout` } : {}), response_types_supported: ["code"], subject_types_supported: ["public"], id_token_signing_alg_values_supported: ["RS256"] });
        if (url.pathname === "/authorize") {
          auth = url.searchParams;
          assert.equal(auth.get("response_type"), "code");
          assert.equal(auth.get("code_challenge_method"), "S256");
          assert(auth.get("scope").includes("api.read"));
          assert.equal(auth.get("redirect_uri"), `${origin}/auth/callback`);
          assert(auth.get("state"));
          return route.fulfill({ status: 302, headers: { location: `${origin}/auth/callback?code=fixture-code&state=${encodeURIComponent(auth.get("state"))}` } });
        }
        if (url.pathname === "/token") {
          const form = new URLSearchParams(route.request().postData());
          assert.equal(form.get("grant_type"), "authorization_code");
          assert.equal(form.get("code"), "fixture-code");
          assert.equal(createHash("sha256").update(form.get("code_verifier")).digest("base64url"), auth.get("code_challenge"));
          const now = Math.floor(Date.now() / 1000);
          const claims = { iss: issuer, sub: "example-user", aud: "aoi-fixture", iat: now, exp: now + 3600, ...(auth.get("nonce") ? { nonce: auth.get("nonce") } : {}) };
          bearer = jwt({ ...claims, aud: "ops-api" });
          exchanges++;
          return reply(route, { access_token: bearer, token_type: "Bearer", expires_in: 3600, id_token: jwt(claims), scope: auth.get("scope") });
        }
        if (url.pathname === "/userinfo") return reply(route, { sub: "example-user", name: "Example member" });
        if (url.pathname === "/logout") {
          logouts++;
          assert(url.searchParams.get("id_token_hint"));
          assert.equal(url.searchParams.get("post_logout_redirect_uri"), origin);
          return route.fulfill({ status: 302, headers: { location: origin } });
        }
        return route.fulfill({ status: 404, body: "Unknown synthetic endpoint" });
      });
      await page.route("**/api/**", route => {
        assert.equal(route.request().headers().authorization, `Bearer ${bearer}`);
        const p = new URL(route.request().url()).pathname;
        const value = p === "/api/account/me" ? { workspace_id: "example-workspace", workspaces: [{ id: "example-workspace", name: "Example company", role: "member" }] }
          : p === "/api/intelligence/dashboard" ? { transcripts: 0, open_tasks: 0, decisions: 0 }
          : p.endsWith("/status") ? { calls_enabled: false, calendar_enabled: false } : { items: [], total: 0 };
        return reply(route, value);
      });
      await page.goto(`${origin}/meetings?tab=huddles`);
      const sso = page.getByRole("button", { name: "Continue with SSO", exact: true });
      await sso.waitFor();
      if (endSession) await page.screenshot({ path: path.join(shots, `sso-login-${name}.png`), fullPage: true });
      await sso.click();
      await page.getByRole("button", { name: "Account", exact: true }).waitFor();
      assert.equal(new URL(page.url()).searchParams.get("tab"), "huddles");
      assert.equal(exchanges, 1);
      assert.equal(await page.getByRole("button", { name: "Huddles", exact: true }).getAttribute("aria-pressed"), "true");
      const logout = page.locator(".header-session-action");
      assert.equal(await logout.innerText(), "Logout");
      assert(await logout.isVisible());
      assert(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), "SSO workspace must fit viewport");
      if (endSession) await page.screenshot({ path: path.join(shots, `logout-header-${name}.png`), fullPage: true });
      await page.reload();
      await logout.waitFor();
      assert.equal(exchanges, 1, "Reload must restore the existing SSO session");
      await logout.click();
      await page.getByRole("button", { name: "Continue with SSO", exact: true }).waitFor();
      assert.equal(logouts, endSession ? 1 : 0);
      assert.equal(await page.evaluate(() => Object.keys(sessionStorage).some(key => key.startsWith("oidc.user:"))), false);
      assert.equal(await page.evaluate(() => localStorage.length), 0);
      // A callback from an unsolicited/forged state must never create a session.
      await page.goto(`${origin}/auth/callback?code=forged&state=unknown`);
      await page.getByRole("alert").waitFor();
      assert.equal(await page.getByRole("button", { name: "Account", exact: true }).count(), 0);
      assert.equal(exchanges, 1);
      assert.equal(errors.length, 0, errors.join("\n"));
      await context.close();
    }
    console.log(`${name}: SSO PKCE exchange, session restore, return route, direct logout, provider/local logout, forged-state rejection passed`);
  }
} finally { await browser.close(); server.kill(); }
