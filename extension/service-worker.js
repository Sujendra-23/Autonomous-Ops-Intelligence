// Background service worker: orchestrates tab-audio capture.
//
// The service worker can't use getUserMedia / AudioContext, so it:
//   1. creates the backend live session (fetch),
//   2. grabs a tab-capture stream id for the active tab,
//   3. spins up an offscreen document that does the actual audio work,
//   4. relays start/stop messages.

// Handle the action ourselves. Chrome's automatic side-panel toggle can open
// the panel without granting the activeTab access required by tabCapture.
// Apply this on worker startup too, replacing the previously persisted setting.
function configureToolbar() {
  return chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: false });
}
configureToolbar().catch((error) => console.warn("Could not configure AOI toolbar:", error.message));
chrome.runtime.onInstalled.addListener(() => configureToolbar().catch(() => {}));
chrome.runtime.onStartup.addListener(() => configureToolbar().catch(() => {}));

chrome.action.onClicked.addListener((tab) => {
  // Call directly in the click handler, before any await, to retain the gesture.
  // The toolbar click grants activeTab; opening the panel does not start recording.
  if (tab.windowId == null) return;
  chrome.sidePanel.open({ windowId: tab.windowId }).catch((error) => {
    console.warn("Could not open AOI side panel:", error.message);
  });
});

async function hasOffscreen() {
  const contexts = await chrome.runtime.getContexts({ contextTypes: ["OFFSCREEN_DOCUMENT"] });
  return contexts.length > 0;
}

let creating = null;
async function ensureOffscreen() {
  if (await hasOffscreen()) return;
  if (!creating) {
    creating = chrome.offscreen.createDocument({
      url: "offscreen.html",
      reasons: ["USER_MEDIA"],
      justification: "Capture and downsample meeting tab audio for live transcription.",
    });
  }
  await creating;
  creating = null;
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg.type === "START") {
    startCapture(msg.config)
      .then((r) => sendResponse(r))
      .catch((e) => sendResponse({ ok: false, error: String(e && e.message ? e.message : e) }));
    return true; // async response
  }
  if (msg.type === "STOP") {
    chrome.runtime.sendMessage({ type: "STOP_CAPTURE" }).catch(() => {});
    sendResponse({ ok: true });
  }
});

async function startCapture(config) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) throw new Error("No active tab to capture — focus the meeting tab first.");
  if (!/^https?:\/\//i.test(tab.url || "")) {
    throw new Error(
      "Open your video or meeting website first, then click the AOI extension icon on that tab. " +
      "Chrome settings, new-tab pages, and extension pages cannot be captured."
    );
  }

  const base = (config.backendUrl || "http://localhost:8000").replace(/\/+$/, "");
  const headers = { "Content-Type": "application/json" };
  if (config.apiKey) headers["X-API-Key"] = config.apiKey;

  const resp = await fetch(`${base}/api/live/sessions`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      title: config.title || tab.title || "Live meeting",
      project_hint: config.projectHint || null,
      calendar_event_id: config.calendarEventId || null,
    }),
  });
  if (!resp.ok) throw new Error(`Backend session create failed (HTTP ${resp.status}).`);
  const data = await resp.json();

  const wsBase = base.replace(/^http/, "ws");
  const wsUrl = `${wsBase}${data.ws_path}`;

  // Must be obtained in the service worker; consumed in the offscreen document.
  let streamId;
  try {
    streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });
  } catch (error) {
    if (/not been invoked|activeTab|Chrome pages cannot be captured/i.test(error.message || "")) {
      throw new Error(
        "Click the AOI extension icon while your video or meeting tab is selected, " +
        "then click Start capturing again. Repeat this after switching tabs."
      );
    }
    throw error;
  }

  await ensureOffscreen();
  chrome.runtime
    .sendMessage({
      type: "START_CAPTURE",
      streamId,
      wsUrl,
      authToken: config.apiKey || null,
      sampleRate: data.sample_rate || 16000,
    })
    .catch(() => {});

  return { ok: true, transcriptId: data.transcript_id, sttEnabled: data.stt_enabled };
}
