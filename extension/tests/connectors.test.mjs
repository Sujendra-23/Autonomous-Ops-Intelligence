import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../sidepanel.js', import.meta.url), 'utf8').split('// Provider sign-in runs')[1];
function harness(fetch) {
  const elements = new Map();
  const tabs = [];
  const windowListeners = {};
  const el = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: '', textContent: '', hidden: true, disabled: false, dataset: {}, listeners: {},
      classList: { toggle() {}, remove() {} },
      addEventListener(event, fn) { this.listeners[event] = fn; },
      setAttribute(name, value) { this[name] = value; }, replaceChildren() {},
    });
    return elements.get(id);
  };
  el('backendUrl').value = 'http://localhost:8000';
  const context = vm.createContext({
    $: el, esc: s => s, fetch, AbortSignal, Date, clearTimeout,
    setTimeout: () => 1,
    Option: function(label, value) { this.label = label; this.value = value; },
    document: {
      querySelector: selector => el(selector),
      querySelectorAll: () => [...elements.values()].filter(e => e.dataset.connect || e.dataset.disconnect),
    },
    window: { addEventListener: (name, fn) => { windowListeners[name] = fn; } },
    chrome: { tabs: { create: async args => { tabs.push(args); return { id: 42 }; }, remove: async () => {}, onRemoved: { addListener() {} } } },
  });
  vm.runInContext('// Provider sign-in runs' + source, context);
  const button = el('[data-connect="google_calendar"]');
  button.dataset.connect = 'google_calendar'; button.textContent = 'Connect Google Calendar';
  const click = () => el('connectorList').listeners.click({ target: { closest: s => s === '[data-connect]' ? button : null } });
  return { el, button, click, tabs, windowListeners };
}
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });

test('Connect immediately shows progress and a network failure beside the clicked button', async () => {
  let reject;
  const h = harness(() => new Promise((_, fail) => { reject = fail; }));
  const pending = h.click();
  assert.equal(h.button.textContent, 'Opening sign-in…');
  assert.equal(h.button.disabled, true);
  assert.equal(h.el('feedback-google_calendar').hidden, false);
  reject(new TypeError('Failed to fetch'));
  await pending;
  assert.match(h.el('feedback-google_calendar').textContent, /Cannot reach the AOI backend at http:\/\/localhost:8000/);
  assert.equal(h.el('feedback-google_calendar').role, 'alert');
  assert.equal(h.el('connectionSettings').open, true);
  assert.equal(h.button.disabled, false);
  assert.equal(h.button.textContent, 'Connect Google Calendar');
  assert.equal(h.tabs.length, 0);
});

test('missing provider credentials show the backend error inline and allow retry', async () => {
  const message = 'Google Calendar sign-in is not configured on this server.';
  const h = harness(async () => response({ detail: message }, 503));
  await h.click();
  assert.equal(h.el('feedback-google_calendar').textContent, message);
  assert.equal(h.button.disabled, false);
  assert.equal(h.tabs.length, 0);
  h.windowListeners.focus();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.el('feedback-google_calendar').textContent, message);
});

test('an old backend returns actionable update instructions', async () => {
  const h = harness(async () => response({ detail: 'Not Found' }, 404));
  await h.click();
  assert.match(h.el('feedback-google_calendar').textContent, /Update and restart/);
  assert.equal(h.button.disabled, false);
});

test('successful initiation opens the returned provider page and waits visibly', async () => {
  const url = 'https://accounts.google.com/o/oauth2/v2/auth?client_id=test';
  const h = harness(async path => response(path.endsWith('/start')
    ? { authorization_url: url, ticket: 'ticket', expires_in: 600 }
    : { status: 'pending' }));
  await h.click();
  assert.equal(h.tabs[0].url, url);
  assert.equal(h.button.textContent, 'Waiting for sign-in…');
  assert.match(h.el('feedback-google_calendar').textContent, /Finish signing in/);
});
