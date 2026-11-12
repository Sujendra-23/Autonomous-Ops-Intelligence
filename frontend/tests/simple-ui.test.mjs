// UI usability regression with mocked records; makes no real workspace/provider writes.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
const origin = process.env.AOI_TEST_URL || "http://127.0.0.1:5173";
const output = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../.impeccable/review",
);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ headless: true });
const id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const future = new Date(Date.now() + 86400000).toISOString();
const past = new Date(Date.now() - 86400000).toISOString();
const notes = [
  {
    id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    title: "Example product planning",
    source: "desktop",
    status: "completed",
    project_id: null,
    meeting_date: past,
    processed_at: past,
    created_at: past,
  },
  {
    id: "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    title: "Example design review",
    source: "live",
    status: "completed",
    project_id: null,
    meeting_date: past,
    processed_at: past,
    created_at: past,
  },
];
try {
  for (const [name, width, height] of [
    ["desktop", 1440, 1000],
    ["mobile", 390, 844],
  ]) {
    const context = await browser.newContext({
      viewport: { width, height },
      reducedMotion: "reduce",
    });
    await context.addInitScript(() => {
      window.__speech = "";
      window.SpeechRecognition = class {
        start() {
          this.onresult?.({
            results: [{ isFinal: true, 0: { transcript: window.__speech } }],
          });
        }
        stop() { this.onend?.(); }
        abort() {}
      };
    });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    let populated = false,
      creates = 0,
      submitted;
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/api/**", (route) => {
      const request = route.request();
      const url = new URL(request.url());
      const p = url.pathname;
      const room = {
        id,
        title: "Example planning call",
        kind: "meeting",
        starts_at: future,
        ends_at: new Date(Date.parse(future) + 1800000).toISOString(),
        time_zone: "America/Los_Angeles",
        description: "",
        attendees: [],
        participants: 0,
        invitation_status: "not_requested",
        calendar_url: null,
        calls_enabled: true,
      };
      let value = {};
      if (p === "/api/intelligence/dashboard")
        value = {
          transcripts: populated ? 2 : 0,
          projects: populated ? 1 : 0,
          open_tasks: populated ? 2 : 0,
          overdue_tasks: populated ? 1 : 0,
          stalled_tasks: 0,
          open_blockers: 0,
          open_risks: 0,
          decisions: populated ? 1 : 0,
        };
      else if (p === "/api/transcripts")
        value = { items: populated ? notes : [], total: populated ? 2 : 0 };
      else if (p === "/api/tasks")
        value = {
          items: populated
            ? [
                {
                  id: "task1",
                  title: "Example: share the prototype",
                  owner: "Sam",
                  due_date: future,
                  priority: "high",
                  status: "open",
                  source: "local",
                },
              ]
            : [],
          total: populated ? 1 : 0,
        };
      else if (p === "/api/meetings/status")
        value = { calls_enabled: true, calendar_enabled: false };
      else if (p === "/api/meetings" && request.method() === "POST") {
        creates++;
        submitted = request.postDataJSON();
        value = { ...room, title: submitted.title, kind: submitted.kind };
      } else if (p === "/api/meetings")
        value = {
          items: populated
            ? [
                {
                  ...room,
                  kind: url.searchParams.get("kind") || "meeting",
                  title:
                    url.searchParams.get("kind") === "huddle"
                      ? "Example team huddle"
                      : room.title,
                },
              ]
            : [],
          has_more: false,
          calls_enabled: true,
          calendar_enabled: false,
        };
      else if (p === `/api/meetings/${id}`)
        value = { ...room, kind: submitted?.kind || "meeting" };
      else if (p.startsWith("/api/transcripts/"))
        value = {
          ...notes[0],
          content: "Example transcript.",
          tasks: [],
          decisions: [],
          risks: [],
          blockers: [],
          segments: [],
        };
      else if (p === "/api/decisions") value = { items: [], total: 0 };
      else if (p === "/api/projects") value = [];
      return route.fulfill({
        contentType: "application/json",
        body: JSON.stringify(value),
      });
    });
    await page.goto(origin);
    await page
      .getByRole("heading", { name: "No meeting notes yet.", exact: true })
      .waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "New transcript", exact: true })
        .count(),
      0,
    );
    await page.screenshot({
      path: path.join(output, `simple-home-empty-${name}.png`),
      fullPage: true,
    });
    if (name === "mobile")
      await page
        .getByRole("button", { name: "Open navigation", exact: true })
        .click();
    const primary = page.getByRole("navigation", {
      name: "Main navigation",
      exact: true,
    });
    assert.equal(await primary.getByRole("button").count(), 5);
    await page.locator(".sidebar-reports > summary").click();
    await page
      .getByRole("navigation", { name: "Reports", exact: true })
      .getByRole("button", { name: "Decisions", exact: true })
      .waitFor();
    if (name === "mobile") {
      const login = await page.locator(".sidebar-session-link").boundingBox();
      assert(
        login && login.y + login.height <= height,
        "Login must fit in the mobile drawer",
      );
      await page.screenshot({
        path: path.join(output, "simple-navigation-mobile.png"),
        fullPage: false,
      });
    }
    await page
      .getByRole("navigation", { name: "Reports", exact: true })
      .getByRole("button", { name: "Decisions", exact: true })
      .click();
    assert.equal(new URL(page.url()).pathname, "/decisions");
    if (name === "mobile")
      await page
        .getByRole("button", { name: "Open navigation", exact: true })
        .click();
    await primary.getByRole("button", { name: "Home", exact: true }).click();
    if (name === "mobile")
      assert.equal(
        await page
          .getByRole("button", { name: "Open navigation", exact: true })
          .getAttribute("aria-expanded"),
        "false",
      );
    populated = true;
    await page
      .getByRole("button", { name: "Refresh home", exact: true })
      .click();
    await page
      .getByRole("button", { name: /Example product planning/ })
      .waitFor();
    assert(await page.evaluate(() => {
      const notes = document.querySelector(".simple-work-grid");
      const totals = document.querySelector(".overview-totals");
      return !!notes && !!totals &&
        !!(notes.compareDocumentPosition(totals) & Node.DOCUMENT_POSITION_FOLLOWING) &&
        notes.getBoundingClientRect().bottom <= totals.getBoundingClientRect().top;
    }), "Meeting notes must precede optional totals in reading and visual order");
    await page.screenshot({
      path: path.join(output, `simple-home-populated-${name}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: "All notes", exact: true }).click();
    assert.equal(new URL(page.url()).searchParams.get("tab"), "captured");
    assert.equal(
      await page
        .getByRole("button", { name: "Meeting notes", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    await page.reload();
    await page.getByRole("heading", { name: /Meeting notes/ }).waitFor();
    assert.equal(
      await page
        .getByRole("button", { name: "Meeting notes", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    await page.screenshot({
      path: path.join(output, `simple-meeting-notes-${name}.png`),
      fullPage: true,
    });
    await page.getByRole("button", { name: /Example product planning/ }).click();
    await page.getByRole("heading", { name: "Example product planning", exact: true }).waitFor();
    await page.getByRole("button", { name: "Meeting notes", exact: true }).click();
    assert.equal(new URL(page.url()).searchParams.get("tab"), "captured");
    await page.getByRole("button", { name: "Upcoming", exact: true }).click();
    await page
      .getByRole("heading", { name: "Example planning call", exact: true })
      .waitFor();
    await page.goBack();
    assert.equal(
      await page
        .getByRole("button", { name: "Meeting notes", exact: true })
        .getAttribute("aria-pressed"),
      "true",
    );
    await page.getByRole("button", { name: "Huddles", exact: true }).click();
    await page
      .getByRole("heading", { name: "Example team huddle", exact: true })
      .waitFor();
    await page.screenshot({
      path: path.join(output, `simple-huddles-${name}.png`),
      fullPage: true,
    });
    await page
      .getByRole("button", { name: "Start huddle", exact: true })
      .click();
    await page
      .getByLabel("Huddle name", { exact: true })
      .fill("Example quick huddle");
    await page.getByRole("button", { name: "Continue", exact: true }).click();
    await page.getByLabel("Your name", { exact: true }).waitFor();
    assert.equal(creates, 1);
    await page
      .getByRole("button", { name: "All meetings", exact: true })
      .click();
    assert.equal(new URL(page.url()).searchParams.get("tab"), "huddles");
    await page.getByRole("button", { name: "Account", exact: true }).click();
    await page
      .getByRole("region", { name: "Account actions" })
      .getByRole("button", { name: "Plans & pricing", exact: true })
      .click();
    assert.equal(new URL(page.url()).pathname, "/pricing");
    await page.getByRole("button", { name: "Talk to AOI", exact: true }).click();
    for (const [command, tab] of [["Go to meeting notes", "captured"], ["Go to huddles", "huddles"]]) {
      await page.evaluate((value) => { window.__speech = value; }, command);
      await page.getByRole("button", { name: "Start listening", exact: true }).click();
      await page.getByRole("button", { name: "Finish & run", exact: true }).click();
      await page.waitForURL(`**/meetings?tab=${tab}`);
    }
    assert.equal(creates, 1, "Navigation commands must not create another room");
    assert.equal(errors.length, 0, errors.join("\n"));
    assert(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    );
    await context.close();
    console.log(
      `${name}: navigation, reports, notes deep-links/back/reload, huddle entry, pricing and speech navigation passed`,
    );
  }
} finally {
  await browser.close();
}
