// Real Electron runtime with synthetic audio and a local mocked AOI backend.
// Does not request microphone/screen permission or record any real call.
import { _electron as electron } from "playwright";
import { createServer } from "node:http";
import { WebSocketServer } from "ws";
import { once } from "node:events";
import { mkdtemp, readFile, rm, mkdir } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const userdata = await mkdtemp(path.join(os.tmpdir(), "aoi-desktop-ui-"));
const id = "11111111-1111-4111-8111-111111111111";
let audioBytes = 0,
  sessionPayload,
  sessionCount = 0,
  offline = false,
  sttEnabled = true;
const server = createServer((request, response) => {
  let text = "";
  request.on("data", (chunk) => (text += chunk));
  request.on("end", () => {
    response.setHeader("Content-Type", "application/json");
    if (offline) {
      response.writeHead(503);
      response.end(JSON.stringify({ detail: "Synthetic backend unavailable" }));
      return;
    }
    if (request.url === "/api/live/sessions") {
      sessionCount++;
      sessionPayload = JSON.parse(text);
      response.end(
        JSON.stringify({
          transcript_id: id,
          ws_path: `/api/live/ws/${id}`,
          stt_enabled: sttEnabled,
          sample_rate: 24000,
          language: "auto",
        }),
      );
    } else
      response.end(
        JSON.stringify({
          items: [{ code: "auto", label: "Auto-detect" }],
          detail: "Connected",
        }),
      );
  });
});
server.listen(0, "127.0.0.1");
await once(server, "listening");
const wss = new WebSocketServer({ server });
wss.on("connection", (socket) => {
  socket.send(JSON.stringify({ type: "ready" }));
  socket.on("message", (data, binary) => {
    if (binary) {
      audioBytes += data.length;
      socket.send(
        JSON.stringify({
          type: "transcript",
          text: "Synthetic call: ship the prototype on Friday.",
          is_final: true,
        }),
      );
    } else if (JSON.parse(data).type === "finalize")
      socket.send(JSON.stringify({ type: "done" }));
  });
});
const env = {
  ...process.env,
  AOI_DESKTOP_TEST: "1",
  AOI_DESKTOP_USER_DATA: userdata,
};
delete env.ELECTRON_RUN_AS_NODE;
let app;
try {
  app = await electron.launch({
    ...(process.env.AOI_DESKTOP_EXECUTABLE
      ? { executablePath: process.env.AOI_DESKTOP_EXECUTABLE, args: [] }
      : { args: [path.join(root, "main.cjs")] }),
    env,
  });
  const page = await app.firstWindow();
  page.setDefaultTimeout(15000);
  await page.emulateMedia({ reducedMotion: "reduce" });
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.locator("#status-label").waitFor();
  await page.waitForFunction(
    () =>
      document.getElementById("backend-url").value === "http://localhost:8000",
  );
  await page.locator("#settings").evaluate((e) => (e.open = true));
  await page
    .locator("#backend-url")
    .fill(`http://127.0.0.1:${server.address().port}`);
  await page.locator("#microphone").uncheck();
  await page
    .getByRole("button", { name: "Save settings", exact: true })
    .click();
  await page.waitForFunction(() =>
    document.getElementById("detail").textContent.includes("Settings saved"),
  );
  await page.locator("#settings").evaluate((e) => (e.open = false));
  await page.evaluate(() => {
    navigator.mediaDevices.getDisplayMedia = async () => {
      const context = new AudioContext();
      const oscillator = context.createOscillator();
      oscillator.frequency.value = 440;
      const destination = context.createMediaStreamDestination();
      oscillator.connect(destination);
      oscillator.start();
      window.testAudio = { context, oscillator };
      return destination.stream;
    };
  });
  await page
    .getByRole("button", { name: "Start recording", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Stop & finalize", exact: true })
    .waitFor();
  await page.waitForFunction(() =>
    document
      .getElementById("transcript")
      .textContent.includes("Synthetic call"),
  );
  assert.equal(sessionPayload.source, "desktop");
  assert.equal(sessionPayload.language, "auto");
  await page
    .getByRole("button", { name: "Stop & finalize", exact: true })
    .click();
  await page.locator("#saved:not([hidden])").waitFor();
  await page.waitForFunction(() => !document.getElementById("toggle").disabled);
  assert(audioBytes > 1000);
  const files = await import("node:fs/promises").then((fs) =>
    fs.readdir(path.join(userdata, "recordings")),
  );
  const wav = await readFile(path.join(userdata, "recordings", files[0]));
  assert.equal(wav.readUInt32LE(40), audioBytes);
  assert.equal(wav.length, 44 + audioBytes);
  assert.equal(errors.length, 0, errors.join("\n"));
  const registered = await app.evaluate(({ globalShortcut }) =>
    globalShortcut.isRegistered("CommandOrControl+Shift+Space"),
  );
  assert.equal(registered, true);
  await page.evaluate(() => window.testAudio.context.close());
  if (process.env.AOI_CAPTURE_SCREENSHOTS === "1") {
    const out = path.join(root, "../.impeccable/review");
    await mkdir(out, { recursive: true });
    await page.locator("main").evaluate((e) => (e.scrollTop = 0));
    await page.screenshot({
      path: path.join(out, "desktop-recorder.png"),
      fullPage: true,
    });
    await page.locator("#settings").evaluate((e) => (e.open = true));
    await page.locator("#settings").scrollIntoViewIfNeeded();
    await page.screenshot({
      path: path.join(out, "desktop-recorder-settings.png"),
      fullPage: true,
    });
    await page.locator("#settings").evaluate((e) => (e.open = false));
    await app.evaluate(({ BrowserWindow }) =>
      BrowserWindow.getAllWindows()[0].setSize(440, 800),
    );
    await page.locator("main").evaluate((e) => (e.scrollTop = 0));
    await page.screenshot({
      path: path.join(out, "desktop-recorder-compact.png"),
      fullPage: true,
    });
  }
  // Capture denial must not create a backend session or a local file.
  const countBeforeDenial = sessionCount;
  await page.evaluate(() => {
    window.syntheticGetDisplayMedia = navigator.mediaDevices.getDisplayMedia;
    navigator.mediaDevices.getDisplayMedia = async () => {
      throw new DOMException("Synthetic permission denied", "NotAllowedError");
    };
  });
  await page
    .getByRole("button", { name: "Start recording", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      !document.getElementById("error").hidden &&
      !document.getElementById("toggle").disabled,
  );
  assert.equal(sessionCount, countBeforeDenial);
  await page.evaluate(() => {
    navigator.mediaDevices.getDisplayMedia = window.syntheticGetDisplayMedia;
  });
  // Offline capture retains a playable WAV when local backup is enabled.
  offline = true;
  await page
    .getByRole("button", { name: "Start recording", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Stop & finalize", exact: true })
    .waitFor();
  await page.waitForFunction(
    () =>
      document.getElementById("status-label").textContent ===
      "Recording locally",
  );
  await page.waitForFunction(
    () => document.getElementById("audio-level").value > 0,
  );
  await page
    .getByRole("button", { name: "Stop & finalize", exact: true })
    .click();
  await page.waitForFunction(() => !document.getElementById("toggle").disabled);
  await page.evaluate(() => window.testAudio.context.close());
  const localFiles = await import("node:fs/promises").then((fs) =>
    fs.readdir(path.join(userdata, "recordings")),
  );
  assert.equal(localFiles.length, 2);
  const localWav = await readFile(
    path.join(
      userdata,
      "recordings",
      localFiles.find((file) => !files.includes(file)),
    ),
  );
  assert(localWav.readUInt32LE(40) > 0);
  assert.equal(localWav.length, 44 + localWav.readUInt32LE(40));
  // No live backend and no backup must never look like a successful recording.
  await page.locator("#local-audio").uncheck();
  await page
    .getByRole("button", { name: "Start recording", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      document
        .getElementById("error")
        .textContent.includes("local audio backup is off") &&
      !document.getElementById("toggle").disabled,
  );
  await page.evaluate(() => window.testAudio.context.close());
  // An unconfigured speech provider is also unsafe without a local backup.
  offline = false;
  sttEnabled = false;
  await page
    .getByRole("button", { name: "Start recording", exact: true })
    .click();
  await page.waitForFunction(
    () =>
      document
        .getElementById("error")
        .textContent.includes("configure backend speech transcription") &&
      !document.getElementById("toggle").disabled,
  );
  await page.evaluate(() => window.testAudio.context.close());
  assert.equal(errors.length, 0, errors.join("\n"));
  console.log(
    JSON.stringify({
      runtime: "Electron",
      syntheticPCMBytes: audioBytes,
      wavBytes: wav.length,
      shortcutRegistered: registered,
      liveFinalize: true,
      deniedCaptureCreatesNoSession: true,
      offlineWavVerified: true,
      noBackupFailuresVisible: true,
      realCallRecorded: false,
    }),
  );
} finally {
  await app?.close();
  for (const client of wss.clients) client.terminate();
  await new Promise((resolve) => wss.close(resolve));
  await new Promise((resolve) => server.close(resolve));
  await rm(userdata, { recursive: true, force: true });
}
