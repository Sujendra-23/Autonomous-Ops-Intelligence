import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const context = vm.createContext({ URL });
vm.runInContext(readFileSync(new URL('../dashboard-link.js', import.meta.url), 'utf8'), context);
const { dashboardLink } = context;

test('opens the local dashboard and deep-links to a captured meeting', () => {
  assert.equal(dashboardLink(' http://localhost:5173/ '), 'http://localhost:5173/');
  assert.equal(dashboardLink('https://ops.example/', 'meeting-123'), 'https://ops.example/meetings/meeting-123');
});

test('strips query credentials and hashes and encodes meeting IDs as one segment', () => {
  assert.equal(dashboardLink('https://ops.example/settings?token=secret#fragment', 'a/b?token=secret'), 'https://ops.example/meetings/a%2Fb%3Ftoken%3Dsecret');
});

test('rejects invalid URLs, executable schemes and embedded credentials', () => {
  for (const value of ['example.com', 'javascript:alert(1)', 'data:text/html,test', 'file:///tmp/notes', 'https://user:secret@ops.example/']) {
    assert.throws(() => dashboardLink(value));
  }
});
