const {
  app,
  BrowserWindow,
  ipcMain,
  globalShortcut,
  Tray,
  Menu,
  nativeImage,
  shell,
  session,
  desktopCapturer,
  safeStorage,
  systemPreferences,
  powerSaveBlocker,
  dialog,
} = require("electron");
const fs = require("node:fs/promises");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const os = require("node:os");
const crypto = require("node:crypto");
const zlib = require("node:zlib");
const { DEFAULTS, normalizeConfig, headers } = require("./lib/config.cjs");
const { WavWriter } = require("./lib/wav.cjs");
const { LiveTransport } = require("./lib/live-transport.cjs");
const APP_FILE = path.join(__dirname, "ui/index.html");
const APP_URL = pathToFileURL(APP_FILE).href;
if (process.env.AOI_DESKTOP_TEST === "1" && process.env.AOI_DESKTOP_USER_DATA)
  app.setPath("userData", process.env.AOI_DESKTOP_USER_DATA);
let finalizationPromise = null;
let configSavePromise = Promise.resolve();
let quitTimer = null;
let window,
  tray,
  config = { ...DEFAULTS },
  token = "",
  recording = null,
  lastRecording = null,
  quitting = false,
  blocker = null,
  shortcutRegistered = false,
  captureAuthorizedUntil = 0;
const status = {
  phase: "idle",
  transport: "idle",
  transcriptId: null,
  detail: "Ready when your call starts.",
};
function emit(data) {
  if (window && !window.isDestroyed())
    window.webContents.send("capture:event", data);
}
function update(patch) {
  Object.assign(status, patch);
  emit({ type: "state", ...status });
  if (tray) {
    tray.setToolTip(
      status.phase === "recording"
        ? "AOI · Recording — press shortcut to stop"
        : "AOI Desktop",
    );
    tray.setImage(trayImage(status.phase === "recording"));
    tray.setContextMenu(
      Menu.buildFromTemplate([
        {
          label:
            status.phase === "recording"
              ? "Stop & finalize recording"
              : "Start recording",
          enabled: !["starting", "finalizing"].includes(status.phase),
          click: () => requestToggle(),
        },
        {
          label: "Open AOI Desktop",
          click: () => {
            window.show();
            window.focus();
          },
        },
        { label: "Open dashboard", click: () => openDashboard() },
        { type: "separator" },
        { label: "Quit AOI Desktop", click: () => app.quit() },
      ]),
    );
  }
}
function crc32(buffer) {
  let n = 0xffffffff;
  for (const value of buffer) {
    n ^= value;
    for (let i = 0; i < 8; i++) n = (n >>> 1) ^ (n & 1 ? 0xedb88320 : 0);
  }
  return (n ^ 0xffffffff) >>> 0;
}
function trayImage(active) {
  const size = 22,
    data = Buffer.alloc((size * 4 + 1) * size);
  const heights = [7, 13, 19, 11, 5];
  for (let y = 0; y < size; y++) {
    data[y * (size * 4 + 1)] = 0;
    for (let x = 0; x < size; x++) {
      const index = Math.floor((x - 2) / 4),
        bar =
          index >= 0 &&
          index < 5 &&
          (x - 2) % 4 < 2 &&
          Math.abs(y - 10) < heights[index] / 2;
      const at = y * (size * 4 + 1) + 1 + x * 4;
      if (bar) {
        data[at] = active ? 181 : 30;
        data[at + 1] = active ? 51 : 30;
        data[at + 2] = active ? 58 : 30;
        data[at + 3] = 255;
      }
    }
  }
  const chunk = (type, bytes) => {
    const name = Buffer.from(type);
    const result = Buffer.alloc(bytes.length + 12);
    result.writeUInt32BE(bytes.length);
    name.copy(result, 4);
    bytes.copy(result, 8);
    result.writeUInt32BE(crc32(Buffer.concat([name, bytes])), 8 + bytes.length);
    return result;
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  const image = nativeImage.createFromBuffer(
    Buffer.concat([
      Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
      chunk("IHDR", ihdr),
      chunk("IDAT", zlib.deflateSync(data)),
      chunk("IEND", Buffer.alloc(0)),
    ]),
  );
  image.setTemplateImage(!active);
  return image;
}
async function readConfig() {
  try {
    const stored = JSON.parse(
      await fs.readFile(
        path.join(app.getPath("userData"), "settings.json"),
        "utf8",
      ),
    );
    config = normalizeConfig(stored.config);
    if (stored.encryptedToken) {
      const decoded = await safeStorage.decryptStringAsync(
        Buffer.from(stored.encryptedToken, "base64"),
      );
      token = decoded.result;
    }
  } catch (error) {
    if (error.code !== "ENOENT")
      status.detail =
        "Saved credentials could not be opened. Check your connection settings.";
  }
}
function saveConfig(input) {
  const result = configSavePromise.then(() => persistConfig(input));
  configSavePromise = result.catch(() => {});
  return result;
}
async function persistConfig(input) {
  if (recording)
    throw new Error("Stop recording before changing connection settings.");
  const next = normalizeConfig(input);
  if (input.token !== undefined && typeof input.token !== "string")
    throw new Error("Invalid credential.");
  const nextToken = input.clearToken ? "" : input.token?.trim() || token;
  if (nextToken.length > 8192) throw new Error("The access token is too long.");
  if (nextToken && !(await safeStorage.isAsyncEncryptionAvailable()))
    throw new Error(
      "Secure credential storage is unavailable. Your token was not saved.",
    );
  let registered = false;
  try {
    registered = globalShortcut.register(next.shortcut, () => requestToggle());
  } catch {
    throw new Error("That shortcut is not valid.");
  }
  if (!registered && next.shortcut !== config.shortcut)
    throw new Error("That shortcut is already in use. Choose another.");
  try {
    const encryptedToken = nextToken
      ? (await safeStorage.encryptStringAsync(nextToken)).toString("base64")
      : null;
    const file = path.join(app.getPath("userData"), "settings.json");
    await fs.mkdir(path.dirname(file), { recursive: true });
    await fs.writeFile(
      file + ".tmp",
      JSON.stringify({ config: next, encryptedToken }),
      { mode: 0o600 },
    );
    await fs.rename(file + ".tmp", file);
  } catch (error) {
    if (registered && next.shortcut !== config.shortcut)
      globalShortcut.unregister(next.shortcut);
    throw error;
  }
  if (next.shortcut !== config.shortcut)
    globalShortcut.unregister(config.shortcut);
  config = next;
  token = nextToken;
  shortcutRegistered =
    registered || globalShortcut.isRegistered(config.shortcut);
  update({ detail: "Settings saved. Your shortcut is ready." });
  return publicConfig();
}
function publicConfig() {
  return {
    ...config,
    hasToken: !!token,
    shortcutRegistered,
    os: process.platform,
    minimumSupported:
      process.platform === "win32" ||
      (process.platform === "darwin" &&
        (Number(os.release().split(".")[0]) > 23 ||
          (Number(os.release().split(".")[0]) === 23 &&
            Number(os.release().split(".")[1]) >= 2))),
    recordingsFolder: path.join(app.getPath("userData"), "recordings"),
    packaged: app.isPackaged,
  };
}
async function api(endpoint, options = {}) {
  const response = await fetch(config.backendUrl + endpoint, {
    ...options,
    headers: { ...headers(config, token), ...options.headers },
    signal: AbortSignal.timeout(15000),
  });
  if (!response.ok)
    throw new Error(
      response.status === 401 || response.status === 403
        ? "Workspace access was denied. Update the API key or workspace token."
        : `Backend request failed (HTTP ${response.status}).`,
    );
  return response.json();
}
async function openDashboard() {
  const meeting = recording
    ? recording.transcriptId
    : lastRecording?.transcriptId || status.transcriptId;
  await shell.openExternal(
    config.dashboardUrl +
      (meeting ? `/meetings/${encodeURIComponent(meeting)}` : ""),
  );
}
function trusted(event) {
  if (
    event.sender !== window?.webContents ||
    event.senderFrame?.url !== APP_URL
  )
    throw new Error("Untrusted desktop request.");
}
function handle(name, callback) {
  ipcMain.handle(name, async (event, ...args) => {
    try {
      trusted(event);
      return { ok: true, value: await callback(...args) };
    } catch (error) {
      return {
        ok: false,
        error: error.message || "The desktop action could not finish.",
      };
    }
  });
}
async function begin() {
  if (recording) throw new Error("A recording is already running.");
  update({
    phase: "starting",
    transport: "connecting",
    detail: "Preparing your desktop recording…",
    transcriptId: null,
  });
  const id = crypto.randomUUID();
  const startedAt = new Date();
  const file = path.join(
    app.getPath("userData"),
    "recordings",
    `${startedAt.toISOString().replace(/[:.]/g, "-")}-${id}.wav`,
  );
  let live = null,
    rate = 24000,
    localReason = "";
  try {
    live = await api("/api/live/sessions", {
      method: "POST",
      body: JSON.stringify({
        title: config.title,
        project_hint: config.projectHint || null,
        language: config.language,
        source: "desktop",
        capture_app: config.platform,
        vocabulary: config.vocabulary
          .split(/[,\n]/)
          .map((term) => term.trim())
          .filter(Boolean),
      }),
    });
    rate = live.sample_rate;
    if (![16000, 24000, 48000].includes(rate))
      throw new Error("The backend returned an unsupported audio sample rate.");
  } catch (error) {
    live = null;
    localReason = error.message;
  }
  if (!config.keepLocalAudio && live && !live.stt_enabled) {
    update({
      phase: "idle",
      transport: "offline",
      detail:
        "Speech transcription is not configured and local audio backup is off.",
    });
    throw new Error(
      "Enable local audio backup or configure backend speech transcription before recording.",
    );
  }
  if (!config.keepLocalAudio && !live) {
    update({ phase: "idle", transport: "offline", detail: localReason });
    throw new Error(
      "The backend is unavailable and local audio backup is off. Enable local recording or reconnect the backend.",
    );
  }
  let writer = null;
  try {
    if (config.keepLocalAudio) {
      await fs.mkdir(path.dirname(file), { recursive: true, mode: 0o700 });
      writer = new WavWriter(file, rate);
      await writer.open();
    }
    recording = {
      id,
      writer,
      sampleRate: rate,
      startedAt: startedAt.toISOString(),
      transport: null,
      transcriptId: live?.transcript_id || null,
      file: writer ? file : null,
      bytes: 0,
      profile: { ...config },
      lastFrame: Date.now(),
      watchdog: null,
    };
    if (live) {
      recording.transport = new LiveTransport({
        base: config.backendUrl,
        path: live.ws_path,
        token,
        workspaceId: config.workspaceId,
        sampleRate: rate,
        onMessage: emit,
        onState: (state) => {
          if (recording?.id !== id) return;
          const failed = state === "overflow" || state === "denied";
          const detail =
            state === "live"
              ? live.stt_enabled
                ? "Live transcription connected."
                : "Recording; speech transcription is not configured on the backend."
              : failed
                ? `${state === "overflow" ? "Live notes have a gap after a long interruption." : "Live transcription access was denied."} ${writer ? "The local recording continues." : "Audio backup is off. Stopping the recording; check the dashboard for notes captured so far."}`
                : "Reconnecting live transcription. Audio is buffered for up to 30 seconds.";
          update({ transport: state, detail });
          if (failed && !writer) emit({ type: "capture-stalled", detail });
        },
      });
      recording.transport.connect();
    }
    if (blocker === null)
      blocker = powerSaveBlocker.start("prevent-app-suspension");
    update({
      phase: "recording",
      transport: live ? "connecting" : "local",
      transcriptId: live?.transcript_id || null,
      detail: live
        ? "Recording desktop audio. Connecting live notes…"
        : `Recording locally. ${localReason} You can upload this WAV later.`,
    });
    recording.watchdog = setInterval(() => {
      if (recording && Date.now() - recording.lastFrame > 10000) {
        update({
          detail:
            "No audio frames are arriving. Check capture permissions and your audio devices.",
        });
        emit({ type: "capture-stalled" });
      }
    }, 5000);
    return {
      id,
      sampleRate: rate,
      transcriptId: recording.transcriptId,
      startedAt: recording.startedAt,
    };
  } catch (error) {
    if (writer) await writer.close().catch(() => {});
    recording = null;
    update({ phase: "idle", detail: error.message });
    throw error;
  }
}
function finish() {
  if (finalizationPromise) return finalizationPromise;
  const current = recording;
  if (!current) return Promise.resolve(lastRecording);
  finalizationPromise = (async () => {
    recording = null;
    clearInterval(current.watchdog);
    update({
      phase: "finalizing",
      detail: "Saving your recording and finalizing notes…",
    });
    let localError = null;
    try {
      if (current.writer) await current.writer.close();
    } catch (error) {
      localError = error.message;
    }
    const finalized = current.transport
      ? await current.transport.finish()
      : false;
    if (blocker !== null) {
      powerSaveBlocker.stop(blocker);
      blocker = null;
    }
    lastRecording = {
      ...current,
      writer: undefined,
      transport: undefined,
      watchdog: undefined,
      finalized,
      localError,
    };
    update({
      phase: "idle",
      transport: "idle",
      transcriptId: current.transcriptId,
    });
    const detail =
      localError ||
      (finalized
        ? "Recording saved. Meeting notes are ready in your workspace."
        : current.file
          ? "Audio saved on this computer. Live notes may be incomplete; check the dashboard."
          : "Recording stopped. Final notes could not be confirmed; check the dashboard.");
    update({ detail });
    emit({
      type: "saved",
      file: current.file,
      transcriptId: current.transcriptId,
      finalized,
      bytes: current.bytes,
    });
    return {
      file: current.file,
      transcriptId: current.transcriptId,
      finalized,
      bytes: current.bytes,
    };
  })().finally(() => {
    finalizationPromise = null;
  });
  return finalizationPromise;
}
async function requestToggle() {
  if (["starting", "finalizing"].includes(status.phase)) return;
  captureAuthorizedUntil = Date.now() + 45000;
  if (!window.isVisible()) window.show();
  window.focus();
  await window.webContents
    .executeJavaScript(
      "window.dispatchEvent(new Event('aoi-capture-toggle'))",
      true,
    )
    .catch(() => {});
}
app.on("before-quit", (event) => {
  if (quitting) return;
  if (finalizationPromise) {
    event.preventDefault();
    quitting = true;
    finalizationPromise.finally(() => app.quit());
    return;
  }
  if (recording) {
    event.preventDefault();
    const choice = dialog.showMessageBoxSync(window, {
      type: "question",
      message: "Stop recording and quit AOI?",
      detail:
        "Your local audio will be saved and live notes will be finalized.",
      buttons: ["Keep recording", "Stop & quit"],
      defaultId: 0,
      cancelId: 0,
    });
    if (choice === 1) {
      quitting = true;
      emit({ type: "quit-stop" });
      quitTimer = setTimeout(() => finish().finally(() => app.quit()), 5000);
    }
    return;
  }
  quitting = true;
});
app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  recording?.transport?.abort();
});
if (!app.requestSingleInstanceLock()) app.quit();
else {
  app.on("second-instance", () => {
    window?.show();
    window?.focus();
  });
  app
    .whenReady()
    .then(async () => {
      await readConfig();
      window = new BrowserWindow({
        width: 560,
        height: 790,
        minWidth: 420,
        minHeight: 620,
        title: "AOI Desktop",
        backgroundColor: "#ffffeb",
        webPreferences: {
          preload: path.join(__dirname, "preload.cjs"),
          contextIsolation: true,
          nodeIntegration: false,
          sandbox: true,
          backgroundThrottling: false,
        },
      });
      window.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
      window.webContents.on("will-navigate", (event, url) => {
        if (url !== APP_URL) event.preventDefault();
      });
      window.on("close", (event) => {
        if (!quitting) {
          event.preventDefault();
          window.hide();
        }
      });
      session.defaultSession.setPermissionCheckHandler(
        (contents, permission, _origin, details) =>
          contents === window.webContents &&
          contents.getURL() === APP_URL &&
          (!details?.requestingUrl || details.requestingUrl === APP_URL) &&
          ["media", "display-capture"].includes(permission),
      );
      session.defaultSession.setPermissionRequestHandler(
        (contents, permission, callback, details) =>
          callback(
            contents === window.webContents &&
              contents.getURL() === APP_URL &&
              (!details?.requestingUrl || details.requestingUrl === APP_URL) &&
              ["media", "display-capture"].includes(permission),
          ),
      );
      session.defaultSession.setDisplayMediaRequestHandler(
        async (request, callback) => {
          if (
            request.frame?.url !== APP_URL ||
            !request.audioRequested ||
            Date.now() > captureAuthorizedUntil
          ) {
            callback(null);
            return;
          }
          captureAuthorizedUntil = 0;
          try {
            const sources = await desktopCapturer.getSources({
              types: ["screen"],
              thumbnailSize: { width: 0, height: 0 },
            });
            callback(
              sources.length ? { video: sources[0], audio: "loopback" } : null,
            );
          } catch {
            callback(null);
          }
        },
      );
      handle("desktop:config", () => publicConfig());
      handle("desktop:state", () => status);
      handle("desktop:save-config", saveConfig);
      handle("desktop:authorize-capture", () => {
        if (!["darwin", "win32"].includes(process.platform))
          throw new Error(
            "Desktop system audio capture is supported on macOS and Windows.",
          );
        captureAuthorizedUntil = Date.now() + 45000;
        return true;
      });
      handle("desktop:begin", begin);
      handle("desktop:finish", async () => {
        const result = await finish();
        if (quitting) {
          clearTimeout(quitTimer);
          setTimeout(() => app.quit(), 100);
        }
        return result;
      });
      handle("desktop:check", async () => {
        const languages = await api("/api/live/languages");
        return { ...languages, detail: "Connected to the saved backend." };
      });
      handle("desktop:dashboard", openDashboard);
      handle("desktop:recordings", async () => {
        const folder = path.join(app.getPath("userData"), "recordings");
        await fs.mkdir(folder, { recursive: true });
        await shell.openPath(folder);
        return true;
      });
      handle("desktop:permissions", () => ({
        microphone: systemPreferences.getMediaAccessStatus("microphone"),
        screen: systemPreferences.getMediaAccessStatus("screen"),
      }));
      handle("desktop:open-permissions", async () => {
        if (process.platform === "darwin")
          await shell.openExternal(
            "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture",
          );
        return true;
      });
      ipcMain.on("desktop:pcm", (event, packet) => {
        try {
          trusted(event);
          if (
            !recording ||
            !(packet instanceof Uint8Array) ||
            packet.byteLength > 65536 ||
            packet.byteLength % 2
          )
            return;
          const data = Buffer.from(packet);
          recording.lastFrame = Date.now();
          recording.bytes += data.length;
          recording.writer?.append(data);
          recording.transport?.send(data);
        } catch {
          update({
            detail:
              "An audio frame could not be saved. Stop and check the recording.",
          });
        }
      });
      tray = new Tray(trayImage(false));
      tray.on("click", () => {
        window.show();
        window.focus();
      });
      try {
        shortcutRegistered = globalShortcut.register(config.shortcut, () =>
          requestToggle(),
        );
      } catch {
        shortcutRegistered = false;
      }
      await window.loadFile(APP_FILE);
      update({
        detail: shortcutRegistered
          ? "Ready when your call starts."
          : "The global shortcut is unavailable. Pick another shortcut in settings.",
      });
      app.on("activate", () => {
        window.show();
        window.focus();
      });
    })
    .catch((error) => {
      dialog.showErrorBox("AOI Desktop could not open", error.message);
      app.quit();
    });
}
