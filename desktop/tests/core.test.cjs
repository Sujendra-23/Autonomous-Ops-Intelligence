const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs/promises");
const os = require("node:os");
const path = require("node:path");
const { once } = require("node:events");
const { WebSocketServer } = require("ws");
const { normalizeConfig, websocketUrl, headers } = require("../lib/config.cjs");
const { WavWriter, header } = require("../lib/wav.cjs");
const { LiveTransport } = require("../lib/live-transport.cjs");
test("connection validation protects credentials and remote transport", () => {
  for (const backendUrl of [
    "http://public.example.com",
    "https://user:password@example.com",
    "https://example.com?token=private",
    "file:///private/file",
  ])
    assert.throws(() => normalizeConfig({ backendUrl }));
  assert.equal(
    normalizeConfig({ backendUrl: "http://localhost:8000/" }).backendUrl,
    "http://localhost:8000",
  );
  assert.equal(
    normalizeConfig({ backendUrl: "https://api.example.com" }).backendUrl,
    "https://api.example.com",
  );
  assert.throws(() =>
    websocketUrl("https://safe.example.com", "https://evil.example.com"),
  );
  assert.equal(
    websocketUrl(
      "https://safe.example.com",
      "/api/live/ws/11111111-1111-4111-8111-111111111111",
    ),
    "wss://safe.example.com/api/live/ws/11111111-1111-4111-8111-111111111111",
  );
});
test("credentials stay in headers, never connection URLs", () => {
  const config = normalizeConfig({
    authMode: "bearer",
    workspaceId: "11111111-1111-4111-8111-111111111111",
  });
  assert.equal(headers(config, "private").Authorization, "Bearer private");
  assert.equal(
    headers(config, "private")["X-Workspace-ID"],
    config.workspaceId,
  );
  assert(
    !websocketUrl(
      config.backendUrl,
      "/api/live/ws/11111111-1111-4111-8111-111111111111",
    ).includes("private"),
  );
});
test("WAV persists every frame in order and patches the final length", async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), "aoi-wav-"));
  try {
    const file = path.join(dir, "call.wav");
    const writer = new WavWriter(file, 24000);
    await writer.open();
    writer.append(Buffer.from([1, 2, 3, 4]));
    writer.append(Buffer.from([5, 6]));
    await writer.close();
    const bytes = await fs.readFile(file);
    assert.equal(bytes.toString("ascii", 0, 4), "RIFF");
    assert.equal(bytes.readUInt32LE(40), 6);
    assert.equal(bytes.readUInt32LE(24), 24000);
    assert.deepEqual([...bytes.subarray(44)], [1, 2, 3, 4, 5, 6]);
    assert.throws(() => writer.append(Buffer.alloc(2)));
    assert.throws(() => header(24000, 3));
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});
test("stream authenticates, waits for ready, preserves queued frames and finalizes", async () => {
  const server = new WebSocketServer({ port: 0 });
  await once(server, "listening");
  const received = [];
  let auth;
  server.on("connection", (socket, request) => {
    assert.equal(request.headers["x-workspace-id"], "workspace");
    socket.on("message", (data, binary) => {
      if (binary) received.push(Buffer.from(data));
      else {
        const event = JSON.parse(data);
        if (event.type === "auth") {
          auth = event.token;
          socket.send(JSON.stringify({ type: "ready" }));
        }
        if (event.type === "finalize")
          socket.send(JSON.stringify({ type: "done" }));
      }
    });
  });
  let ready;
  const connected = new Promise((resolve) => (ready = resolve));
  const transport = new LiveTransport({
    base: `http://127.0.0.1:${server.address().port}`,
    path: "/api/live/ws/11111111-1111-4111-8111-111111111111",
    token: "private",
    workspaceId: "workspace",
    sampleRate: 16000,
    onMessage: () => {},
    onState: (state) => {
      if (state === "live") ready();
    },
  });
  transport.send(Buffer.from([1, 2]));
  transport.connect();
  await connected;
  transport.send(Buffer.from([3, 4]));
  assert.equal(await transport.finish(), true);
  assert.equal(auth, "private");
  assert.deepEqual([...Buffer.concat(received)], [1, 2, 3, 4]);
  await new Promise((resolve) => server.close(resolve));
});
test("bounded queue reports audio gaps instead of dropping them silently", () => {
  const states = [];
  const transport = new LiveTransport({
    base: "http://localhost:8000",
    path: "/api/live/ws/11111111-1111-4111-8111-111111111111",
    sampleRate: 16000,
    onMessage: () => {},
    onState: (s) => states.push(s),
  });
  for (let i = 0; i < 20; i++) transport.send(Buffer.alloc(65536));
  assert.equal(transport.dead, true);
  assert.deepEqual(states, ["overflow"]);
  assert(transport.queued <= 16000 * 2 * 30);
});
test("backpressure queue drains older frames before new ones", () => {
  const transport = new LiveTransport({
    base: "http://localhost:8000",
    path: "/api/live/ws/11111111-1111-4111-8111-111111111111",
    sampleRate: 16000,
    onMessage: () => {},
    onState: () => {},
  });
  const sent = [];
  transport.ready = true;
  transport.socket = {
    readyState: 1,
    bufferedAmount: 3 * 1024 * 1024,
    send: (frame) => sent.push([...frame]),
  };
  transport.send(Buffer.from([1, 2]));
  transport.socket.bufferedAmount = 0;
  transport.send(Buffer.from([3, 4]));
  assert.deepEqual(sent, [
    [1, 2],
    [3, 4],
  ]);
});
test("WAV writer handles partial operating-system writes without dropping samples", async () => {
  const { writeAll } = require("../lib/wav.cjs");
  const received = [];
  const handle = {
    write: async (buffer, offset, length, position) => {
      const count = Math.min(2, length);
      received.push({
        bytes: [...buffer.subarray(offset, offset + count)],
        position,
      });
      return { bytesWritten: count };
    },
  };
  await writeAll(handle, Buffer.from([1, 2, 3, 4, 5, 6]), 44);
  assert.deepEqual(received, [
    { bytes: [1, 2], position: 44 },
    { bytes: [3, 4], position: 46 },
    { bytes: [5, 6], position: 48 },
  ]);
});
test("audio worklet flush preserves the trailing partial batch and reports clipping", async () => {
  const vm = require("node:vm");
  const source = await fs.readFile(
    path.join(__dirname, "../ui/pcm-worklet.js"),
    "utf8",
  );
  let Processor;
  const sent = [];
  const context = {
    AudioWorkletProcessor: class {
      constructor() {
        this.port = { postMessage: (value) => sent.push(value) };
      }
    },
    registerProcessor: (_name, constructor) => (Processor = constructor),
    Int16Array,
    Math,
  };
  vm.runInNewContext(source, context);
  const worklet = new Processor();
  worklet.process([[Float32Array.from([0.5, -0.5, 1.4])]]);
  worklet.port.onmessage({ data: "flush" });
  const audio = sent.find((value) => value instanceof ArrayBuffer);
  assert.equal(audio.byteLength, 6);
  assert.deepEqual([...new Int16Array(audio)], [16384, -16384, 32767]);
  assert(sent.some((value) => value.quality?.clippedFraction > 0));
  assert.equal(sent.at(-1).flushed, true);
});
