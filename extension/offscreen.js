// Offscreen document: the only place that can use getUserMedia + AudioContext.
//
// Pipeline:  tab stream -> 16 kHz AudioContext -> PCM worklet -> WebSocket
// We also route the stream to the speakers so the user still hears the meeting
// (tab capture mutes the tab's normal output).

let ws = null;
let audioCtx = null;
let source = null;
let worklet = null;
let stream = null;

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "START_CAPTURE") startCapture(msg);
  else if (msg.type === "STOP_CAPTURE") stopCapture();
});

function toPanel(data) {
  chrome.runtime.sendMessage({ type: "WS_MESSAGE", data }).catch(() => {});
}

async function startCapture({ streamId, wsUrl }) {
  try {
    stream = await navigator.mediaDevices.getUserMedia({
      audio: { mandatory: { chromeMediaSource: "tab", chromeMediaSourceId: streamId } },
    });
  } catch (e) {
    toPanel({ type: "error", detail: "Audio capture failed: " + e });
    return;
  }

  ws = new WebSocket(wsUrl);
  ws.binaryType = "arraybuffer";
  ws.onmessage = (ev) => {
    try {
      toPanel(JSON.parse(ev.data));
    } catch (_) {
      /* ignore non-JSON */
    }
  };
  ws.onerror = () => toPanel({ type: "error", detail: "WebSocket error" });
  ws.onclose = () => toPanel({ type: "closed" });

  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    setTimeout(() => reject(new Error("ws timeout")), 8000);
  }).catch(() => toPanel({ type: "error", detail: "Could not reach backend WebSocket" }));

  audioCtx = new AudioContext({ sampleRate: 16000 });
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
