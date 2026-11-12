// Requires the isolated test backend/database and a local LiveKit server.
// Fake camera/microphone and generated canvas sharing: never captures real devices/screens.
import { chromium } from "playwright";
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { mkdir } from "node:fs/promises";
const origin = process.env.AOI_CALL_TEST_URL || "http://127.0.0.1:5193";
const out = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../../.impeccable/review",
);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  args: [
    "--use-fake-ui-for-media-stream",
    "--use-fake-device-for-media-stream",
  ],
});
async function participant() {
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    reducedMotion: "reduce",
    permissions: ["microphone", "camera"],
  });
  await context.addInitScript(() => {
    window.__testPeers = [];
    const Original = window.RTCPeerConnection;
    window.RTCPeerConnection = class extends Original {
      constructor(...args) {
        super(...args);
        window.__testPeers.push(this);
      }
    };
    navigator.mediaDevices.getDisplayMedia = async () => {
      const canvas = document.createElement("canvas");
      canvas.width = 640;
      canvas.height = 360;
      const ctx = canvas.getContext("2d");
      let frame = 0;
      const draw = () => {
        ctx.fillStyle = frame++ % 2 ? "#efd5fc" : "#fafaf0";
        ctx.fillRect(0, 0, 640, 360);
        ctx.fillStyle = "#1a1a1a";
        ctx.font = "30px sans-serif";
        ctx.fillText("Synthetic AOI screen share", 40, 180);
      };
      draw();
      const timer = setInterval(draw, 120);
      const stream = canvas.captureStream(8);
      stream
        .getVideoTracks()[0]
        .addEventListener("ended", () => clearInterval(timer));
      window.__testCanvas = canvas;
      return stream;
    };
  });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  return { context, page };
}
async function join(page, name) {
  await page.getByLabel("Your name", { exact: true }).fill(name);
  await page.getByRole("button", { name: /^Join (meeting|huddle)$/ }).click();
  await page
    .locator(".call-room-status")
    .getByText("Connected", { exact: true })
    .waitFor({ timeout: 45000 });
}
async function incoming(page, kind) {
  await page.waitForFunction(
    async (kind) => {
      for (const peer of window.__testPeers) {
        const stats = await peer.getStats();
        for (const item of stats.values())
          if (
            item.type === "inbound-rtp" &&
            item.kind === kind &&
            item.bytesReceived > 0
          )
            return true;
      }
      return false;
    },
    kind,
    { timeout: 30000 },
  );
}
try {
  const alice = await participant();
  const bob = await participant();
  await alice.page.goto(`${origin}/meetings`);
  await alice.page
    .getByRole("button", { name: "Huddles", exact: true })
    .click();
  await alice.page
    .getByRole("button", { name: "Start huddle", exact: true })
    .click();
  await alice.page
    .getByLabel("Huddle name", { exact: true })
    .fill("Synthetic team huddle");
  await alice.page
    .getByRole("button", { name: "Continue", exact: true })
    .click();
  await alice.page.getByLabel("Your name", { exact: true }).waitFor();
  const url = alice.page.url();
  assert(url.includes("/meetings/rooms/"));
  await alice.page.screenshot({
    path: path.join(out, "call-prejoin-desktop.png"),
    fullPage: true,
  });
  await join(alice.page, "Alice test");
  await bob.page.goto(url);
  await join(bob.page, "Bob test");
  await alice.page.getByText("People (2)", { exact: true }).waitFor();
  const presence = await alice.page.request.get(
    `${origin}/api/meetings?kind=huddle`,
  );
  const presenceRows = (await presence.json()).items;
  let count = presenceRows.find((item) => url.endsWith(item.id)).participants;
  for (let attempt = 0; attempt < 30 && count !== 2; attempt++) {
    await new Promise((resolve) => setTimeout(resolve, 500));
    count = (
      await (
        await alice.page.request.get(`${origin}/api/meetings?kind=huddle`)
      ).json()
    ).items.find((item) => url.endsWith(item.id)).participants;
  }
  assert.equal(count, 2);
  await incoming(alice.page, "audio");
  await incoming(bob.page, "audio");
  assert.equal(
    await alice.page
      .locator('.lk-control-bar [data-lk-source="camera"]')
      .count(),
    0,
  );
  await alice.page
    .getByRole("button", { name: "Share screen", exact: true })
    .click();
  await incoming(bob.page, "video");
  await bob.page.locator("video").filter({ visible: true }).first().waitFor();
  await alice.page.getByRole("button", { name: "Chat", exact: true }).click();
  await alice.page
    .getByPlaceholder("Enter a message...")
    .fill("Synthetic collaboration message");
  await alice.page.getByPlaceholder("Enter a message...").press("Enter");
  await bob.page.getByRole("button", { name: /^Chat/ }).click();
  await bob.page
    .getByText("Synthetic collaboration message", { exact: true })
    .waitFor();
  await bob.page
    .getByRole("button", { name: /Close chat/ })
    .click()
    .catch(() => {});
  await alice.page.screenshot({
    path: path.join(out, "huddle-screen-desktop.png"),
    fullPage: false,
  });
  await bob.page.setViewportSize({ width: 390, height: 844 });
  assert(
    await bob.page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
    "Mobile call must fit",
  );
  await bob.page.waitForFunction(() => {
    const box = document
      .querySelector(".lk-control-bar")
      ?.getBoundingClientRect();
    return box && box.bottom <= innerHeight && box.top >= 0;
  });
  await bob.page.screenshot({
    path: path.join(out, "huddle-mobile.png"),
    fullPage: false,
  });
  await alice.page
    .getByRole("button", { name: "Stop screen share", exact: true })
    .click();
  await alice.page.getByRole("button", { name: "Leave", exact: true }).click();
  await alice.page.getByLabel("Your name", { exact: true }).waitFor();
  await bob.page.getByText("People (1)", { exact: true }).waitFor();
  assert(
    await alice.page.evaluate(() =>
      window.__testPeers.every((peer) => peer.connectionState === "closed"),
    ),
    "Leaving closes peer connections",
  );
  await bob.page.getByRole("button", { name: "Leave", exact: true }).click();
  await bob.context.close();
  await alice.page.goto(`${origin}/meetings`);
  await alice.page
    .getByRole("button", { name: "Schedule", exact: true })
    .click();
  await alice.page
    .getByLabel("Meeting title", { exact: true })
    .fill("Synthetic scheduled planning");
  const future = new Date(Date.now() + 2 * 86400000);
  const date = `${future.getFullYear()}-${String(future.getMonth() + 1).padStart(2, "0")}-${String(future.getDate()).padStart(2, "0")}`;
  await alice.page.getByLabel("Date", { exact: true }).fill(date);
  await alice.page.getByLabel("Start time", { exact: true }).fill("14:30");
  await alice.page.getByText("More options", { exact: true }).click();
  await alice.page
    .getByLabel("Time zone", { exact: true })
    .fill("Asia/Kolkata");
  await alice.page.getByText("More options", { exact: true }).click();
  assert(
    (await alice.page.locator(".schedule-zone-caption").textContent()).includes(
      "Asia/Kolkata",
    ),
  );
  await alice.page.screenshot({
    path: path.join(out, "schedule-desktop.png"),
    fullPage: true,
  });
  await alice.page.setViewportSize({ width: 390, height: 844 });
  await alice.page.screenshot({
    path: path.join(out, "schedule-mobile.png"),
    fullPage: true,
  });
  await alice.page
    .getByRole("button", { name: "Schedule meeting", exact: true })
    .click();
  await alice.page
    .getByText("Meeting scheduled in AOI.", { exact: true })
    .waitFor();
  await alice.page
    .locator(".scheduled-confirmation")
    .getByRole("button", { name: "Join meeting", exact: true })
    .click();
  await alice.page.getByLabel("Your name", { exact: true }).waitFor();
  const scheduledId = new URL(alice.page.url()).pathname.split("/").at(-1);
  const persisted = await (
    await alice.page.request.get(`${origin}/api/meetings/${scheduledId}`)
  ).json();
  assert.equal(persisted.time_zone, "Asia/Kolkata");
  await alice.page.screenshot({
    path: path.join(out, "call-prejoin-mobile.png"),
    fullPage: true,
  });
  await alice.page.getByLabel("Camera on", { exact: true }).check();
  await join(alice.page, "Alice camera test");
  await alice.page
    .locator('.lk-control-bar [data-lk-source="camera"]')
    .waitFor();
  const cameraPeer = await participant();
  await cameraPeer.page.goto(alice.page.url());
  await join(cameraPeer.page, "Bob camera receiver");
  await incoming(cameraPeer.page, "video");
  await alice.page.setViewportSize({ width: 1440, height: 1000 });
  await alice.page.screenshot({
    path: path.join(out, "video-meeting-desktop.png"),
    fullPage: false,
  });
  await cameraPeer.page
    .getByRole("button", { name: "Leave", exact: true })
    .click();
  await cameraPeer.context.close();
  await alice.page.getByRole("button", { name: "Leave", exact: true }).click();
  await alice.context.close();
  console.log(
    JSON.stringify({
      realBackend: true,
      realDatabase: true,
      realLiveKit: true,
      participants: 2,
      syntheticAudioTransport: true,
      syntheticScreenTransport: true,
      chatDelivery: true,
      huddleCameraDisabled: true,
      meetingCameraSupported: true,
      leaveClosesPeers: true,
      schedulingPersisted: true,
      realDevicesCaptured: false,
    }),
  );
} finally {
  await browser.close();
}
