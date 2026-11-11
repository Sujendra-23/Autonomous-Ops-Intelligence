const WebSocket = require("ws");
const { websocketUrl } = require("./config.cjs");
// Retain bounded audio through reconnects. Overflow is visible and recording stays local.
class LiveTransport {
  constructor({
    base,
    path,
    token,
    workspaceId,
    sampleRate,
    onMessage,
    onState,
  }) {
    this.url = websocketUrl(base, path);
    this.token = token;
    this.workspaceId = workspaceId;
    this.rate = sampleRate;
    this.onMessage = onMessage;
    this.onState = onState;
    this.queue = [];
    this.queued = 0;
    this.ready = false;
    this.stopping = false;
    this.dead = false;
    this.attempt = 0;
    this.socket = null;
    this.timer = null;
    this.done = null;
  }
  connect() {
    if (this.dead) return;
    const ws = new WebSocket(this.url, {
      headers: this.workspaceId ? { "X-Workspace-ID": this.workspaceId } : {},
      handshakeTimeout: 10000,
      maxPayload: 8 * 1024 * 1024,
    });
    this.socket = ws;
    ws.on("open", () => {
      if (this.token)
        ws.send(JSON.stringify({ type: "auth", token: this.token }));
    });
    ws.on("message", (data) => {
      let message;
      try {
        message = JSON.parse(data.toString());
      } catch {
        return;
      }
      if (message.type === "ready") {
        this.ready = true;
        this.attempt = 0;
        this.onState("live");
        this.drain();
        if (this.stopping) ws.send(JSON.stringify({ type: "finalize" }));
      }
      this.onMessage(message);
      if (message.type === "done") {
        this.dead = true;
        this.done?.(true);
        ws.close();
      }
    });
    ws.on("error", () => this.onState("reconnecting"));
    ws.on("close", (code) => {
      this.ready = false;
      if (this.dead) return;
      if (code === 1008) {
        this.dead = true;
        this.onState("denied");
        this.done?.(false);
        return;
      }
      if (this.stopping) {
        this.dead = true;
        this.done?.(false);
        return;
      }
      this.onState("reconnecting");
      this.timer = setTimeout(
        () => this.connect(),
        Math.min(1000 * 2 ** this.attempt++, 15000),
      );
    });
  }
  drain() {
    while (
      this.ready &&
      this.socket?.readyState === WebSocket.OPEN &&
      this.socket.bufferedAmount < 2 * 1024 * 1024 &&
      this.queue.length
    ) {
      const frame = this.queue.shift();
      this.queued -= frame.length;
      this.socket.send(frame, { binary: true });
    }
  }
  send(data) {
    if (this.dead) return;
    const frame = Buffer.from(data);
    if (frame.length % 2 || frame.length > 65536)
      throw new Error("Invalid audio frame.");
    if (this.queued + frame.length > this.rate * 2 * 30) {
      this.dead = true;
      clearTimeout(this.timer);
      this.socket?.close();
      this.onState("overflow");
      return;
    }
    this.queue.push(frame);
    this.queued += frame.length;
    this.drain();
  }
  async finish() {
    this.stopping = true;
    clearTimeout(this.timer);
    if (this.dead) return false;
    return await new Promise((resolve) => {
      const timeout = setTimeout(() => {
        this.dead = true;
        this.socket?.close();
        resolve(false);
      }, 60000);
      this.done = (result) => {
        clearTimeout(timeout);
        resolve(result);
      };
      if (this.ready && this.socket?.readyState === WebSocket.OPEN) {
        this.drain();
        if (this.queue.length) {
          this.dead = true;
          this.socket.close();
          clearTimeout(timeout);
          resolve(false);
          return;
        }
        this.socket.send(JSON.stringify({ type: "finalize" }));
      } else {
        this.dead = true;
        this.socket?.close();
        clearTimeout(timeout);
        resolve(false);
      }
    });
  }
  abort() {
    this.dead = true;
    clearTimeout(this.timer);
    this.socket?.close();
    this.done?.(false);
  }
}
module.exports = { LiveTransport };
