// Real browser UI with isolated local Vite instances and mocked identity/API.
// No real credentials, identity service, database, or microphone are used.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { mkdir } from "node:fs/promises";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const servers = [];
const browser = await chromium.launch({ headless: true });
const shots = path.join(root, "../.impeccable/review");
await mkdir(shots, { recursive: true });
async function start(port, provider) {
  const child = spawn(
    process.execPath,
    [
      path.join(root, "node_modules/vite/bin/vite.js"),
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
      "--strictPort",
    ],
    {
      cwd: root,
      env: {
        ...process.env,
        VITE_AUTH_PROVIDER: provider,
        VITE_AUTH_AUTHORITY: "",
        VITE_AUTH_CLIENT_ID: "",
        VITE_API_URL: "",
      },
      stdio: "pipe",
    },
  );
  servers.push(child);
  let log = "";
  child.stdout.on("data", (value) => {
    log += value;
  });
  child.stderr.on("data", (value) => {
    log += value;
  });
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null)
      throw new Error(`Isolated Vite exited: ${log}`);
    try {
      if ((await fetch(`http://127.0.0.1:${port}`)).ok)
        return `http://127.0.0.1:${port}`;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("Isolated Vite did not start");
}
async function mock(page) {
  const state = {
    company: "alpha",
    token: "",
    failedLogout: false,
    logouts: 0,
    logins: 0,
    meHeaders: [],
  };
  const reply = (route, value, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(value),
    });
  await page.route("**/auth/gateway/**", async (route) => {
    const request = route.request();
    const endpoint = new URL(request.url()).pathname.split("/").at(-1);
    const body = request.postDataJSON();
    if (endpoint === "login") {
      state.logins++;
      if (body.password !== "fixture-password")
        return reply(route, { detail: "Invalid credentials" }, 401);
      state.company = body.company_slug;
      state.token = `synthetic-${state.company}-token`;
      return reply(route, {
        access_token: state.token,
        expires_at: Math.floor(Date.now() / 1000) + 3600,
        company_slug: state.company,
      });
    }
    if (endpoint === "logout") {
      state.logouts++;
      if (state.failedLogout) {
        state.failedLogout = false;
        return reply(route, { detail: "Synthetic outage" }, 503);
      }
      return reply(route, { signed_out: true });
    }
    return reply(route, { detail: "No saved session" }, 401);
  });
  await page.route("**/api/**", async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname === "/api/account/me") {
      state.meHeaders.push(route.request().headers());
      return reply(route, {
        account_id: `${state.company}-user`,
        workspace_id: `${state.company}-workspace`,
        role: "owner",
        workspaces: [
          {
            id: `${state.company}-workspace`,
            name: `${state.company} workspace`,
            role: "owner",
          },
        ],
      });
    }
    if (pathname === "/api/transcripts")
      return reply(route, { items: [], total: 0, page: 1, page_size: 20 });
    if (pathname === "/api/tasks") return reply(route, { items: [], total: 0 });
    if (pathname === "/api/projects" || pathname === "/api/decisions")
      return reply(route, []);
    if (pathname.includes("connectors")) return reply(route, { items: [] });
    if (
      pathname.includes("counts") ||
      pathname === "/api/intelligence/dashboard"
    )
      return reply(route, {
        transcripts: 0,
        projects: 0,
        open_tasks: 0,
        overdue_tasks: 0,
        stalled_tasks: 0,
        open_blockers: 0,
        open_risks: 0,
        decisions: 0,
      });
    return reply(route, {});
  });
  return state;
}
async function login(page, company, password = "fixture-password") {
  await page.getByLabel("Company code", { exact: true }).fill(company);
  await page
    .getByLabel("Work email", { exact: true })
    .fill("person@example.test");
  await page.getByLabel("Password", { exact: true }).fill(password);
  await page
    .getByRole("button", { name: "Login to your company", exact: true })
    .click();
}
async function noOverflow(page) {
  assert(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "Horizontal page overflow",
  );
}
try {
  const local = await start(5191, "");
  const company = await start(5192, "gateway");
  for (const [name, width, height] of [
    ["desktop", 1440, 1000],
    ["mobile", 390, 844],
  ]) {
    const context = await browser.newContext({
      viewport: { width, height },
      reducedMotion: "reduce",
    });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [];
    page.on("pageerror", (e) => errors.push(e.message));
    const state = await mock(page);
    await page.goto(`${company}/extension`);
    await page.getByLabel("Company code", { exact: true }).waitFor();
    await noOverflow(page);
    await page.screenshot({
      path: path.join(shots, `login-${name}.png`),
      fullPage: true,
    });
    await login(page, "alpha", "wrong-password");
    await page
      .getByRole("alert")
      .filter({ hasText: "Check your company code" })
      .waitFor();
    await login(page, "alpha");
    await page.getByRole("button", { name: "Account", exact: true }).waitFor();
    assert.equal(
      new URL(page.url()).pathname,
      "/extension",
      "Meeting/navigation return path preserved",
    );
    await page.getByRole("button", { name: "Account", exact: true }).click();
    await page
      .getByRole("region", { name: "Account actions" })
      .getByText("alpha workspace", { exact: true })
      .waitFor();
    await noOverflow(page);
    await page.screenshot({
      path: path.join(shots, `account-${name}.png`),
      fullPage: false,
    });
    await page.keyboard.press("Escape");
    assert(
      await page
        .getByRole("button", { name: "Account", exact: true })
        .evaluate((e) => e === document.activeElement),
    );
    await page.getByRole("button", { name: "Account", exact: true }).click();
    state.failedLogout = true;
    await page
      .getByRole("region", { name: "Account actions" })
      .getByRole("button", { name: "Logout", exact: true })
      .click();
    await page
      .getByRole("region", { name: "Account actions" })
      .getByRole("alert")
      .waitFor();
    await page
      .getByRole("region", { name: "Account actions" })
      .getByRole("button", { name: "Logout", exact: true })
      .click();
    await page.getByLabel("Company code", { exact: true }).waitFor();
    assert.equal(new URL(page.url()).pathname, "/login");
    assert.equal(
      await page.evaluate(() => sessionStorage.getItem("aoi-company")),
      null,
    );
    await login(page, "beta");
    await page.getByRole("button", { name: "Account", exact: true }).waitFor();
    await page.waitForFunction(() =>
      document
        .querySelector(".workspace-switcher")
        ?.textContent.includes("beta workspace"),
    );
    const latest = state.meHeaders.at(-1);
    assert.equal(latest.authorization, "Bearer synthetic-beta-token");
    assert(
      !latest["x-workspace-id"],
      "Previous company's workspace must not leak into new login",
    );
    assert.equal(
      await page.evaluate(() =>
        Object.values({ ...sessionStorage, ...localStorage }).some(
          (value) =>
            String(value).includes("synthetic-") ||
            String(value).includes("fixture-password"),
        ),
      ),
      false,
    );
    await page
      .getByRole("button", { name: "Talk to AOI", exact: true })
      .click();
    await page.getByLabel("Your command", { exact: true }).fill("logout");
    await page
      .getByRole("button", { name: "Run command", exact: true })
      .click();
    await page.getByLabel("Company code", { exact: true }).waitFor();
    assert.equal(state.logouts, 3);
    assert.equal(errors.length, 0, errors.join("\n"));
    await context.close();
    const localContext = await browser.newContext({
      viewport: { width, height },
      reducedMotion: "no-preference",
    });
    const localPage = await localContext.newPage();
    localPage.setDefaultTimeout(10000);
    await mock(localPage);
    await localPage.addInitScript(() => {
      window.__flowAnimations = [];
      const animate = Element.prototype.animate;
      Element.prototype.animate = function (frames, options) {
        if (frames[0]?.clipPath)
          window.__flowAnimations.push({ frames, options });
        return animate.call(this, frames, options);
      };
    });
    await localPage.goto(`${local}/extension`);
    await localPage
      .getByRole("button", { name: "Account", exact: true })
      .click();
    await localPage
      .getByRole("region", { name: "Account actions" })
      .getByRole("link", { name: "Login", exact: true })
      .click();
    await localPage
      .getByText("This installation is using a local workspace.", {
        exact: false,
      })
      .waitFor();
    assert.equal(
      await localPage.locator('input[type="password"]').count(),
      0,
      "No fake login in local mode",
    );
    await localPage.goto(`${local}/extension`);
    await localPage.locator(".desktop-capture-setup").waitFor();
    await localPage
      .locator(".browser-capture-setup")
      .evaluate((e) => (e.open = true));
    await localPage.locator(".browser-capture-setup").scrollIntoViewIfNeeded();
    await localPage.waitForFunction(
      () =>
        Number(
          document
            .querySelector(".studio-content")
            .style.getPropertyValue("--workspace-progress"),
        ) > 0,
    );
    await localPage.waitForFunction(() =>
      document.querySelector('[data-flow-state="reached"]'),
    );
    assert(
      await localPage.evaluate(() => window.__flowAnimations.length > 0),
      "Scroll section should actually animate",
    );
    await localPage.waitForTimeout(550);
    await noOverflow(localPage);
    await localPage.screenshot({
      path: path.join(shots, `scroll-flow-${name}.png`),
      fullPage: false,
    });
    await localPage.emulateMedia({ reducedMotion: "reduce" });
    assert.equal(
      await localPage
        .locator(".workspace-scroll-progress")
        .evaluate((e) => getComputedStyle(e).display),
      "none",
    );
    assert.equal(
      await localPage
        .locator("#workspace-main")
        .evaluate(
          (e) =>
            e
              .getAnimations({ subtree: true })
              .filter((a) => a.playState === "running").length,
        ),
      0,
    );
    if (name === "mobile")
      await localPage
        .getByRole("button", { name: "Open navigation", exact: true })
        .click();
    await localPage
      .getByRole("navigation", { name: "Main navigation" })
      .getByRole("button", { name: "Dashboard", exact: true })
      .click();
    await localPage.waitForFunction(() => window.scrollY === 0);
    assert.equal(
      await localPage
        .getByRole("button", { name: "Back to top", exact: true })
        .count(),
      0,
      "A new view must clear the previous page's return control",
    );
    await localContext.close();
    console.log(
      `${name}: login, invalid credentials, logout retry, account switch, voice logout, local mode, scroll flow and reduced motion passed`,
    );
  }
} finally {
  await browser.close();
  for (const server of servers) server.kill("SIGTERM");
}
