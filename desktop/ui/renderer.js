const $ = (id) => document.getElementById(id);
let preferences = null,
  busy = false,
  capturing = false,
  startedAt = null,
  stream = null,
  microphone = null,
  audio = null,
  worklet = null,
  nodes = [],
  flushDone = null,
  captureFailure = null;
let currentState = { phase: "idle" };
let finalizedText = "",
  interimText = "";
function error(message) {
  $("error").textContent = message || "";
  $("error").hidden = !message;
}
function detail(message) {
  $("detail").textContent = message;
}
function profile() {
  return {
    ...preferences,
    title: $("title").value,
    vocabulary: $("vocabulary").value,
    projectHint: $("project").value,
    language: $("language").value,
    includeMic: $("microphone").checked,
    keepLocalAudio: $("local-audio").checked,
    platform: $("platform").value,
  };
}
function renderState(state) {
  currentState = state;
  $("status-label").textContent =
    state.phase === "recording"
      ? state.transport === "local"
        ? "Recording locally"
        : state.transport === "live"
          ? "Recording · Live notes connected"
          : "Recording · Connecting notes"
      : state.phase === "finalizing"
        ? "Finalizing your meeting"
        : state.phase === "starting"
          ? "Preparing your recording"
          : "Ready to record";
  $("status").classList.toggle("is-recording", state.phase === "recording");
  if (state.detail) detail(state.detail);
}
function lock(value) {
  $("recording-fields").disabled = value;
  $("settings-fields").disabled = value;
  $("vocabulary").disabled = value;
}
async function load() {
  preferences = await window.desktop.config();
  $("title").value = preferences.title;
  $("vocabulary").value = preferences.vocabulary;
  $("project").value = preferences.projectHint;
  $("language").value = preferences.language;
  $("microphone").checked = preferences.includeMic;
  $("local-audio").checked = preferences.keepLocalAudio;
  $("platform").value = preferences.platform;
  $("backend-url").value = preferences.backendUrl;
  $("dashboard-url").value = preferences.dashboardUrl;
  $("auth-mode").value = preferences.authMode;
  $("workspace-id").value = preferences.workspaceId;
  $("shortcut").value = preferences.shortcut;
  $("shortcut-label").textContent = preferences.shortcut
    .replace("CommandOrControl", preferences.os === "darwin" ? "⌘" : "Ctrl")
    .replace("Shift", "⇧")
    .replaceAll("+", " ");
  $("token-hint").textContent = preferences.hasToken
    ? "A credential is saved securely. Leave blank to keep it."
    : "Stored encrypted using your operating system.";
  $("recording-folder").textContent = preferences.recordingsFolder;
  $("platform-note").textContent =
    preferences.os === "win32"
      ? "Desktop capture · Windows"
      : "Desktop capture · macOS 14.2+";
  $("open-permissions").hidden = preferences.os !== "darwin";
  if (!preferences.minimumSupported)
    error("This system audio capture path requires macOS 14.2+ or Windows.");
  if (!preferences.shortcutRegistered)
    error(
      "The global shortcut is unavailable. Choose another shortcut in settings.",
    );
  renderState(await window.desktop.state());
}
async function cleanup() {
  for (const media of [stream, microphone])
    media?.getTracks().forEach((track) => track.stop());
  for (const node of nodes)
    try {
      node.disconnect();
    } catch {}
  nodes = [];
  stream = microphone = null;
  if (audio) {
    await audio.close().catch(() => {});
    audio = null;
  }
  worklet = null;
}
async function toggle() {
  if (busy) return;
  if (capturing) {
    await stop();
    return;
  }
  busy = true;
  captureFailure = null;
  error("");
  $("toggle").disabled = true;
  $("toggle").querySelector("span").textContent = "Preparing…";
  lock(true);
  try {
    preferences = await window.desktop.saveConfig(profile());
    await window.desktop.authorizeCapture();
    stream = await navigator.mediaDevices.getDisplayMedia({
      audio: {
        echoCancellation: false,
        noiseSuppression: false,
        autoGainControl: false,
      },
      video: { width: 16, height: 16, frameRate: 1 },
    });
    const systemAudio = stream.getAudioTracks();
    if (
      !systemAudio.length ||
      systemAudio.some((track) => track.readyState !== "live")
    )
      throw new Error(
        "System audio permission is missing or the audio track ended. Allow AOI Desktop in recording permissions, then reopen the app.",
      );
    // Never display, encode or send screen frames.
    stream.getVideoTracks().forEach((track) => track.stop());
    if ($("microphone").checked)
      microphone = await navigator.mediaDevices.getUserMedia({
        video: false,
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    const session = await window.desktop.begin();
    audio = new AudioContext({ sampleRate: session.sampleRate });
    await audio.audioWorklet.addModule("pcm-worklet.js");
    const systemSource = audio.createMediaStreamSource(
      new MediaStream(systemAudio),
    );
    const mix = audio.createGain();
    mix.gain.value = microphone ? 0.7 : 1;
    systemSource.connect(mix);
    nodes.push(systemSource, mix);
    if (microphone) {
      const micSource = audio.createMediaStreamSource(microphone);
      micSource.connect(mix);
      nodes.push(micSource);
    }
    worklet = new AudioWorkletNode(audio, "desktop-pcm");
    worklet.port.onmessage = (event) => {
      if (event.data?.flushed) {
        flushDone?.();
        flushDone = null;
      } else if (event.data instanceof ArrayBuffer)
        window.desktop.pcm(event.data);
      else if (event.data?.quality) {
        const quality = event.data.quality;
        $("audio-level").value = Math.min(1, quality.rms * 4);
        $("audio-quality-label").textContent =
          quality.clippedFraction > 0.02
            ? "Audio is clipping. Lower system or microphone volume."
            : quality.rms < 0.0005
              ? "No audible signal right now. Check that your call audio is playing."
              : "Audio signal detected.";
      }
    };
    const silentOutput = audio.createGain();
    silentOutput.gain.value = 0;
    mix.connect(worklet);
    worklet.connect(silentOutput);
    silentOutput.connect(audio.destination);
    nodes.push(worklet, silentOutput);
    await audio.resume();
    if (captureFailure) throw new Error(captureFailure);
    capturing = true;
    $("audio-quality").hidden = false;
    $("transcript-review").hidden = true;
    startedAt = new Date(session.startedAt).getTime();
    finalizedText = interimText = "";
    $("live-preview").hidden = false;
    $("saved").hidden = true;
    $("transcript").textContent =
      "Speech will appear here when the backend is transcribing.";
    $("toggle").classList.add("is-recording");
    $("toggle").querySelector("span").textContent = "Stop & finalize";
    for (const track of [
      ...systemAudio,
      ...(microphone?.getAudioTracks() || []),
    ])
      track.addEventListener(
        "ended",
        () => {
          if (capturing) {
            error(
              "An audio source ended. Recording has stopped. Check permissions and your devices before restarting.",
            );
            void stop();
          }
        },
        { once: true },
      );
  } catch (cause) {
    await cleanup();
    await window.desktop.finish().catch(() => {});
    error(cause.message || "Audio capture could not start.");
    lock(false);
    $("toggle").querySelector("span").textContent = "Start recording";
  } finally {
    busy = false;
    if (captureFailure && capturing) void stop();
    $("toggle").disabled = false;
  }
}
async function stop() {
  if (busy) return;
  busy = true;
  capturing = false;
  $("toggle").disabled = true;
  $("toggle").querySelector("span").textContent = "Saving…";
  try {
    if (worklet) {
      await new Promise((resolve) => {
        flushDone = resolve;
        worklet.port.postMessage("flush");
        setTimeout(resolve, 750);
      });
    }
    await cleanup();
    await window.desktop.finish();
  } catch (cause) {
    error(cause.message || "The recording could not be fully saved.");
  } finally {
    busy = false;
    startedAt = null;
    $("audio-quality").hidden = true;
    lock(false);
    $("toggle").disabled = false;
    $("toggle").classList.remove("is-recording");
    $("toggle").querySelector("span").textContent = "Start recording";
  }
}
window.desktop.onEvent((event) => {
  if (event.type === "state") renderState(event);
  else if (event.type === "transcript") {
    if (event.needs_review) $("transcript-review").hidden = false;
    if (event.is_final) {
      finalizedText = (finalizedText + " " + event.text).trim().slice(-10000);
      interimText = "";
    } else interimText = event.text;
    $("transcript").textContent = [finalizedText, interimText]
      .filter(Boolean)
      .join(" ");
    $("transcript").scrollTop = $("transcript").scrollHeight;
  } else if (event.type === "notes") {
    $("note-count").textContent =
      `${event.tasks?.length || 0} tasks · ${event.decisions?.length || 0} decisions`;
  } else if (event.type === "error")
    error(event.detail || "Live transcription reported an error.");
  else if (event.type === "saved") {
    $("saved").hidden = false;
    $("saved-detail").textContent = event.file
      ? `Audio saved locally (${(event.bytes / 1024 / 1024).toFixed(1)} MB). ${event.finalized ? "Live notes finalized." : "Check the dashboard for any live notes; upload the WAV if needed."}`
      : "Audio backup was off. Check the dashboard for your live transcript.";
    $("view-meeting").hidden = !event.transcriptId;
    $("show-recordings").hidden = !event.file;
  } else if (event.type === "capture-stalled") {
    captureFailure =
      event.detail ||
      "Audio capture stopped producing samples. Saving the recording so far.";
    error(captureFailure);
    if (capturing) void stop();
  } else if (event.type === "quit-stop" && capturing) void stop();
});
$("toggle").addEventListener("click", () => void toggle());
window.addEventListener("aoi-capture-toggle", () => void toggle());
$("settings-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  error("");
  try {
    preferences = await window.desktop.saveConfig({
      ...profile(),
      backendUrl: $("backend-url").value,
      dashboardUrl: $("dashboard-url").value,
      authMode: $("auth-mode").value,
      workspaceId: $("workspace-id").value,
      shortcut: $("shortcut").value,
      token: $("auth-token").value,
      clearToken: $("clear-token").checked,
    });
    $("auth-token").value = "";
    $("clear-token").checked = false;
    await load();
    detail("Settings saved. Your shortcut is ready.");
  } catch (cause) {
    error(cause.message);
  }
});
$("check-connection").addEventListener("click", async () => {
  error("");
  $("check-connection").disabled = true;
  try {
    const result = await window.desktop.check();
    const selected = $("language").value;
    $("language").replaceChildren(
      ...result.items.map((item) => {
        const option = document.createElement("option");
        option.value = item.code;
        option.textContent = item.label;
        return option;
      }),
    );
    $("language").value = selected;
    detail(result.detail);
  } catch (cause) {
    error(cause.message);
  } finally {
    $("check-connection").disabled = false;
  }
});
for (const id of ["dashboard", "view-meeting"])
  $(id).addEventListener("click", () =>
    window.desktop.openDashboard().catch((cause) => error(cause.message)),
  );
for (const id of ["show-recordings", "open-recordings"])
  $(id).addEventListener("click", () =>
    window.desktop.openRecordings().catch((cause) => error(cause.message)),
  );
$("open-permissions").addEventListener("click", () =>
  window.desktop.openPermissions().catch((cause) => error(cause.message)),
);
$("permissions").addEventListener("toggle", async () => {
  if (!$("permissions").open) return;
  try {
    const result = await window.desktop.permissions();
    $("permission-state").textContent =
      `Microphone: ${result.microphone}. Screen/system audio: ${result.screen}.`;
  } catch {}
});
setInterval(() => {
  const seconds = startedAt
    ? Math.max(0, Math.floor((Date.now() - startedAt) / 1000))
    : 0;
  $("timer").textContent =
    `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
}, 1000);
load().catch((cause) => error(cause.message));
