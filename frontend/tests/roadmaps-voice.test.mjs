// Isolated browser regression: simulated speech recognition and mocked API/provider responses.
// Requires the Vite dev server; no live records or provider calls are made.
import { chromium } from "playwright";
import assert from "node:assert/strict";
const browser = await chromium.launch({ headless: true });
const origin = process.env.AOI_TEST_URL || "http://127.0.0.1:5173";
const projectId = "11111111-1111-4111-8111-111111111111";
const meetingId = "22222222-2222-4222-8222-222222222222";
const goal =
  "Launch the customer portal. Milestones: Prototype and Release. Tasks: Build the prototype, Review the prototype, Publish the release.";
const base = {
  summary: "Launch a customer portal, from prototype to release.",
  assumptions: ["The proposed timing needs team review."],
  horizon_weeks: 4,
  start_date: "2026-11-16",
  source_type: "manual",
  source_plan: goal,
  source_meeting_ids: [],
  generated_at: "2026-11-11T20:00:00Z",
  milestones: [
    {
      title: "Prototype",
      objective: "Validate the customer experience.",
      start_week: 1,
      end_week: 2,
      owner: null,
      deliverables: ["Build the prototype", "Review the prototype"],
      depends_on: [],
      status: "planned",
      task_ids: [],
    },
    {
      title: "Release",
      objective: "Put the approved experience in customers’ hands.",
      start_week: 3,
      end_week: 4,
      owner: "Sam",
      deliverables: ["Publish the release"],
      depends_on: [1],
      status: "planned",
      task_ids: [],
    },
  ],
};
let failures = [];
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
      stop() {
        this.onend?.();
      }
      abort() {}
    };
  });
  const page = await context.newPage();
  page.on("pageerror", (error) => failures.push(error.message));
  let saved = null,
    saveCount = 0,
    generated = 0,
    assistantCalls = 0;
  const reply = (route, data, status = 200) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body: JSON.stringify(data),
    });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const body = route.request().postDataJSON?.();
    if (path === "/api/projects")
      return reply(route, [
        {
          id: projectId,
          name: "Customer portal",
          slug: "portal",
          description: null,
          status: "active",
          notion_page_id: null,
          linear_project_id: null,
          has_roadmap: !!saved,
        },
      ]);
    if (path === "/api/transcripts")
      return reply(route, {
        items: [
          {
            id: meetingId,
            title: "Portal planning",
            status: "completed",
            source: "upload",
            project_id: projectId,
            created_at: "2026-11-11T20:00:00Z",
            meeting_date: null,
            processed_at: null,
          },
        ],
        total: 1,
      });
    if (path.endsWith("/roadmap") && route.request().method() === "GET")
      return reply(
        route,
        saved ?? {
          project_id: projectId,
          name: "Customer portal",
          version: 0,
          roadmap: null,
        },
      );
    if (path === "/api/projects/roadmap/generate") {
      generated++;
      return reply(route, {
        ...base,
        source_type: body.source_type,
        source_plan: body.plan,
        source_meeting_ids: body.source_type === "manual" ? [] : [meetingId],
        horizon_weeks: body.horizon_weeks,
      });
    }
    if (path === "/api/projects/roadmap") {
      saveCount++;
      saved = {
        project_id: projectId,
        name: body.name,
        version: saveCount,
        created_task_count: saveCount === 1 && body.create_tasks ? 3 : 0,
        roadmap: {
          ...body.roadmap,
          milestones: body.roadmap.milestones.map((m, i) => ({
            ...m,
            task_ids: m.deliverables.map((_, j) => `${i}-${j}`),
          })),
        },
      };
      return reply(route, saved);
    }
    if (path === "/api/assistant/command") {
      assistantCalls++;
      const controls = body.controls;
      const by = (label) => controls.find((c) => c.label.startsWith(label));
      if (body.view !== "roadmaps")
        return reply(route, {
          message: "Opening Roadmaps.",
          actions: [{ kind: "navigate", target: "roadmaps", value: "" }],
          done: false,
        });
      if (body.page_text.includes("Roadmap saved."))
        return reply(route, {
          message: "Created your roadmap and three project tasks.",
          actions: [],
          done: true,
        });
      const actions = [];
      if (by("Project name") && !by("Project name").value)
        actions.push({
          kind: "fill",
          target: by("Project name").id,
          value: "Customer portal",
        });
      if (
        by("Your goal, milestones, and tasks") &&
        by("Your goal, milestones, and tasks").value !== goal
      )
        actions.push({
          kind: "fill",
          target: by("Your goal, milestones, and tasks").id,
          value: goal,
        });
      if (actions.length)
        return reply(route, {
          message: "Adding your goal, milestones and tasks.",
          actions,
          done: false,
        });
      if (body.page_text.includes("Unsaved draft"))
        return reply(route, {
          message: "Saving your roadmap and creating the tasks.",
          actions: [
            { kind: "click", target: by("Save roadmap & tasks").id, value: "" },
          ],
          done: false,
        });
      return reply(route, {
        message: "Generating your roadmap.",
        actions: [
          { kind: "click", target: by("Generate roadmap").id, value: "" },
        ],
        done: false,
      });
    }
    return reply(route, {}, 503);
  });
  await page.goto(origin + "/roadmaps");
  await page.evaluate(() => document.fonts.ready);
  await page
    .getByLabel("Project name", { exact: true })
    .fill("Customer portal");
  await page
    .getByLabel("Your goal, milestones, and tasks", { exact: true })
    .fill(goal);
  await page.getByLabel("Planning horizon (weeks)").fill("4");
  await page
    .getByRole("button", { name: "Generate roadmap", exact: true })
    .click();
  await page.getByText("Unsaved draft", { exact: true }).waitFor();
  assert.equal(generated, 1);
  await page.evaluate(() => window.scrollTo(0, 0));
  await page
    .getByRole("button", { name: "Save roadmap & tasks", exact: true })
    .click();
  await page
    .getByText("Roadmap saved. 3 tasks created in your project.", {
      exact: true,
    })
    .waitFor();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page
    .getByLabel("Status of Prototype", { exact: true })
    .selectOption("done");
  await page
    .getByRole("button", { name: "Save roadmap & tasks", exact: true })
    .click();
  assert.equal(saved.roadmap.milestones[0].status, "done");
  assert.equal(saveCount, 2);
  assert.equal(
    await page.evaluate(() => document.documentElement.scrollWidth),
    width,
  );
  // Exercise the real browser speech adapter and local navigation.
  await page.getByRole("button", { name: "Talk to AOI", exact: true }).click();
  await page.evaluate(() => (window.__speech = "Go to tasks"));
  await page
    .getByRole("button", { name: "Start listening", exact: true })
    .click();
  await page.getByRole("button", { name: "Finish & run", exact: true }).click();
  await page.waitForURL("**/tasks");
  // Full voice-driven roadmap creation with a mocked planner and generation provider.
  saved = null;
  saveCount = 0;
  await page.evaluate(
    () =>
      (window.__speech =
        "Go to Roadmaps and create a customer portal goal with Prototype and Release milestones and create the tasks I described."),
  );
  await page
    .getByRole("button", { name: "Start listening", exact: true })
    .click();
  await page.getByRole("button", { name: "Finish & run", exact: true }).click();
  await page
    .getByText("Created your roadmap and three project tasks.", { exact: true })
    .waitFor({ timeout: 20000 });
  assert.equal(saveCount, 1);
  assert(assistantCalls >= 4);
  await page.evaluate(() => window.scrollTo(0, 0));
  // Dictation updates React form state, not just the DOM value.
  await page
    .getByRole("button", { name: "Close voice assistant", exact: true })
    .click();
  await page.getByText("Plan a new version", { exact: true }).click();
  await page.locator("#roadmap-plan").focus();
  await page.getByRole("button", { name: "Talk to AOI", exact: true }).click();
  await page
    .getByRole("button", { name: "Dictate into a field", exact: true })
    .click();
  await page.evaluate(
    () => (window.__speech = "Include a pilot with the design team."),
  );
  await page
    .getByRole("button", { name: "Start listening", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Finish dictation", exact: true })
    .click();
  assert(
    (await page.locator("#roadmap-plan").inputValue()).includes(
      "Include a pilot",
    ),
  );
  await page
    .getByRole("button", { name: "Close voice assistant", exact: true })
    .click();
  await page
    .getByRole("button", { name: "A meeting transcript", exact: true })
    .click();
  await page.locator("#roadmap-meeting").selectOption(meetingId);
  await page.evaluate(() => window.scrollTo(0, 0));

  await page
    .getByRole("button", { name: "Generate roadmap", exact: true })
    .click();
  await page.getByText("Unsaved draft", { exact: true }).waitFor();
  await page
    .getByRole("button", { name: "Discard draft", exact: true })
    .click();
  await page.getByText("Plan a new version", { exact: true }).click();
  await page
    .getByRole("button", { name: "Project meetings", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Generate roadmap", exact: true })
    .click();
  await page.getByText("Unsaved draft", { exact: true }).waitFor();
  assert.equal(generated, 4);
  console.log(
    name,
    JSON.stringify({
      saveCount,
      assistantCalls,
      generated,
      overflow: false,
      speech: "mocked recognition; real handlers",
      sources: "manual, transcript, project",
    }),
  );
  await context.close();
}
await browser.close();
assert.deepEqual(failures, []);
console.log("All browser checks passed.");
