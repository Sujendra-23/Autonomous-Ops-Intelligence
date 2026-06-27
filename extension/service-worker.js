// Background service worker: orchestrates tab-audio capture.
//
// The service worker can't use getUserMedia / AudioContext, so it:
//   1. creates the backend live session (fetch),
//   2. grabs a tab-capture stream id for the active tab,
//   3. spins up an offscreen document that does the actual audio work,
//   4. relays start/stop messages.

chrome.runtime.onInstalled.addListener(() => {
  // Clicking the toolbar icon opens the side panel.
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
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

  const base = (config.backendUrl || "http://localhost:8000").replace(/\/+$/, "");
  const headers = { "Content-Type": "application/json" };
  if (config.apiKey) headers["X-API-Key"] = config.apiKey;

  const resp = await fetch(`${base}/api/live/sessions`, {
    method: "POST",
    headers,
    body: JSON.stringify({
      title: config.title || tab.title || "Live meeting",
      project_hint: config.projectHint || null,
    }),
  });
  if (!resp.ok) throw new Error(`Backend session create failed (HTTP ${resp.status}).`);
  const data = await resp.json();

  const wsBase = base.replace(/^http/, "ws");
  const qs = config.apiKey ? `?api_key=${encodeURIComponent(config.apiKey)}` : "";
  const wsUrl = `${wsBase}${data.ws_path}${qs}`;

  // Must be obtained in the service worker; consumed in the offscreen document.
  const streamId = await chrome.tabCapture.getMediaStreamId({ targetTabId: tab.id });

  await ensureOffscreen();
  chrome.runtime.sendMessage({ type: "START_CAPTURE", streamId, wsUrl }).catch(() => {});

  return { ok: true, transcriptId: data.transcript_id, sttEnabled: data.stt_enabled };
}
