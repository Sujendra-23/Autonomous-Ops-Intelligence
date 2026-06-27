// Offscreen document: the only place that can use getUserMedia + AudioContext.
//
// Pipeline:  tab stream -> AudioContext(sampleRate) -> PCM worklet -> WebSocket
// We also route the stream to the speakers so the user still hears the meeting
// (tab capture mutes the tab's normal output).
//
// The WebSocket auto-reconnects with backoff if it drops mid-meeting; the audio
// pipeline keeps running and resumes sending once the socket is back. The
// backend resumes the same session (it re-seeds from the persisted transcript),
// so no notes are lost across a reconnect.

let ws = null;
let audioCtx = null;
let source = null;
let worklet = null;
let stream = null;

let wsUrl = null;
let stopping = false;
let reconnectAttempt = 0;
let reconnectTimer = null;
const MAX_RECONNECT_DELAY = 15000;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "START_CAPTURE") startCapture(msg);
  else if (msg.type === "STOP_CAPTURE") stopCapture();
});

function toPanel(data) {
  chrome.runtime.sendMessage({ type: "WS_MESSAGE", data }).catch(() => {});
}

function connectWs() {
  ws = new WebSocket(wsUrl);
  ws.binaryType = "arraybuffer";

  ws.onopen = () => {
    if (reconnectAttempt > 0) toPanel({ type: "reconnected" });
    reconnectAttempt = 0;
  };
  ws.onmessage = (ev) => {
    try {
      toPanel(JSON.parse(ev.data));
    } catch (_) {
      /* ignore non-JSON */
    }
  };
  ws.onerror = () => toPanel({ type: "error", detail: "WebSocket error" });
  ws.onclose = () => {
    if (stopping) {
      toPanel({ type: "closed" });
      return;
    }
    scheduleReconnect();
  };
}

function scheduleReconnect() {
  reconnectAttempt += 1;
  const delay = Math.min(1000 * 2 ** (reconnectAttempt - 1), MAX_RECONNECT_DELAY);
  toPanel({ type: "reconnecting", attempt: reconnectAttempt, delayMs: delay });
  reconnectTimer = setTimeout(() => {
    if (!stopping) connectWs();
  }, delay);
}

async function startCapture({ streamId, wsUrl: url, sampleRate }) {
  stopping = false;
  reconnectAttempt = 0;
  wsUrl = url;

  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    });
  } catch (e) {
    toPanel({ type: "error", detail: "Audio capture failed: " + e });
    return;
  }

  connectWs();

  audioCtx = new AudioContext({ sampleRate: sampleRate || 16000 });
  await audioCtx.audioWorklet.addModule("pcm-worklet.js");
  source = audioCtx.createMediaStreamSource(stream);
  worklet = new AudioWorkletNode(audioCtx, "pcm-worklet");
  worklet.port.onmessage = (e) => {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(e.data);
  };
  source.connect(worklet);
  source.connect(audioCtx.destination); // keep meeting audible
  toPanel({ type: "capturing" });
}

function stopCapture() {
  stopping = true;
  if (reconnectTimer) clearTimeout(reconnectTimer);
  try {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "finalize" }));
  } catch (_) {
    /* noop */
  }
  try {
    worklet && worklet.disconnect();
  } catch (_) {
    /* noop */
  }
  try {
    source && source.disconnect();
  } catch (_) {
    /* noop */
  }
  try {
    stream && stream.getTracks().forEach((t) => t.stop());
  } catch (_) {
    /* noop */
  }
  try {
    audioCtx && audioCtx.close();
  } catch (_) {
    /* noop */
  }
  // Give the backend a moment to run the final extraction pass before closing.
  setTimeout(() => {
    try {
      ws && ws.close();
    } catch (_) {
      /* noop */
    }
    ws = audioCtx = source = worklet = stream = null;
  }, 1500);
}
