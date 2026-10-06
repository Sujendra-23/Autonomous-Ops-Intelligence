import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../service-worker.js', import.meta.url), 'utf8');

function harness({ url = 'https://www.youtube.com/watch?v=test', captureError } = {}) {
  const listeners = {};
  const calls = [];
  const event = (name) => ({ addListener(fn) { listeners[name] = fn; } });
  const chrome = {
    runtime: {
      onInstalled: event('installed'), onStartup: event('startup'), onMessage: event('message'),
      getContexts: async () => [{}],
      sendMessage: async (message) => { calls.push(['message', message]); },
    },
    action: { onClicked: event('clicked') },
    sidePanel: {
      setPanelBehavior: async (options) => { calls.push(['behavior', options]); },
      open: async (options) => { calls.push(['open', options]); },
    },
    tabs: { query: async () => [{ id: 42, windowId: 7, url, title: 'Test video' }] },
    tabCapture: { getMediaStreamId: async (options) => {
      calls.push(['capture', options]);
      if (captureError) throw new Error(captureError);
      return 'stream';
    } },
  };
  const context = vm.createContext({ chrome, console, fetch: async () => {
    calls.push(['fetch']);
    return { ok: true, json: async () => ({ id: 'session', ws_path: '/api/live/ws/session', sample_rate: 24000, stt_enabled: true }) };
  } });
  vm.runInContext(source, context);
  return { context, listeners, calls };
}

test('replaces the persisted automatic-panel behavior on worker startup and install', async () => {
  const { calls, listeners } = harness();
  assert.equal(calls[0][1].openPanelOnActionClick, false);
  await listeners.installed();
  await listeners.startup();
  assert.equal(calls.filter(([name]) => name === 'behavior').length, 3);
  assert.ok(calls.every(([, value]) => value.openPanelOnActionClick === false));
});

test('toolbar click opens the clicked window synchronously without capturing or contacting backend', () => {
  const { calls, listeners } = harness();
  listeners.clicked({ id: 42, windowId: 7 });
  assert.equal(calls.at(-1)[0], 'open');
  assert.equal(calls.at(-1)[1].windowId, 7);
  assert.equal(calls.length, 2);
});

test('rejects Chrome pages before backend session creation', async () => {
  const { context, calls } = harness({ url: 'chrome://extensions' });
  await assert.rejects(context.startCapture({}), /Open your video or meeting website/);
  assert.ok(!calls.some(([name]) => name === 'fetch'));
});

test('reports permission denial rather than starting the offscreen capture', async () => {
  const { context, calls } = harness({ captureError: 'Extension has not been invoked for the current page (see activeTab permission)' });
  await assert.rejects(context.startCapture({}), /Click the AOI extension icon/);
  assert.ok(!calls.some(([name]) => name === 'message'));
});

test('Start captures the selected tab and forwards its stream and provider sample rate', async () => {
  const { context, calls } = harness();
  assert.equal((await context.startCapture({})).ok, true);
  assert.equal(calls.find(([name]) => name === 'capture')[1].targetTabId, 42);
  const message = calls.find(([name]) => name === 'message')[1];
  assert.equal(message.type, 'START_CAPTURE');
  assert.equal(message.streamId, 'stream');
  assert.equal(message.sampleRate, 24000);
});

test('workspace tokens stay out of WebSocket URLs and travel in the authentication frame config', async () => {
  const { context, calls } = harness();
  await context.startCapture({ apiKey: 'aoi_test-workspace-token', backendUrl: 'https://studio.example' });
  const message = calls.find(([name]) => name === 'message')[1];
  assert.equal(message.wsUrl, 'wss://studio.example/api/live/ws/session');
  assert.equal(message.authToken, 'aoi_test-workspace-token');
  assert.ok(!message.wsUrl.includes('aoi_'));
});
