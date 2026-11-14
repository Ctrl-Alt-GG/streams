const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const { classes } = require('./fake-dom.cjs');

const script = readFileSync('src/catalog/static/catalog/js/catalog.js', 'utf8');

function element(dataset = {}) {
  return {
    textContent: '', dataset, classList: classes(), attributes: {},
    setAttribute(name, value) { this.attributes[name] = value; },
  };
}

const stream = (overrides = {}) => ({
  id: 'id', status: 'live', audio_only: false, effective_name: 'Main stage', description: '',
  ...overrides,
});
const entry = (item) => [item.id, item.status, item.audio_only, item.effective_name, item.description];
const answer = (body) => ({ ok: true, status: 200, json: async () => body });
const detail = (source, item) => answer({ source: { status: source }, result: item });
const list = (source, items) => answer({ source: { status: source }, results: items });

// Runs catalog.js on a page showing `streams`; `poll(response)` runs one status check.
function catalogPage({ url, streams, streamPage = false, player = false, title = 'Streams' }) {
  const page = { reloads: 0, title, timers: [], requests: [] };
  const warning = element({ staleMessage: 'stale', unavailableMessage: 'unavailable' });
  const names = [element(), element()];
  const label = element({ streamNameLabel: 'Player' });
  const document = {
    hidden: false,
    get title() { return page.title; },
    set title(value) { page.title = value; },
    getElementById(id) {
      return id === 'catalog-watch-state' ? { textContent: JSON.stringify({ url, streams }) } : null;
    },
    querySelector(selector) {
      return {
        '[data-stream-id]': streamPage ? element() : null,
        '[data-hls-player]': player ? element() : null,
        '[data-source-warning]': warning,
      }[selector] ?? null;
    },
    querySelectorAll(selector) {
      return { '[data-stream-name]': names, '[data-stream-name-label]': player ? [label] : [] }[selector] ?? [];
    },
  };
  const window = {
    setTimeout(callback, delay) { page.timers.push({ callback, delay }); return page.timers.length; },
    clearTimeout() {},
    addEventListener() {},
    location: { reload() { page.reloads += 1; } },
  };
  let response;
  vm.runInNewContext(script, {
    document, window, AbortSignal,
    fetch: async (requested) => { page.requests.push(requested); return response; },
  });
  return {
    page, warning, names, label,
    async poll(next) { response = next; await page.timers.at(-1).callback(); },
  };
}

const streamPage = (item, options = {}) => catalogPage({
  url: '/api/v1/streams/id/', streams: [entry(item)], streamPage: true, ...options,
});

test('a live stream keeps playing while the status source is unavailable or stale', async () => {
  const live = stream();
  const { page, warning, poll } = streamPage(live, { player: true });
  await poll(detail('unavailable', stream({ status: 'unknown' })));
  assert.equal(page.reloads, 0);
  assert.equal(warning.classList.contains('hidden'), false);
  assert.equal(warning.textContent, 'unavailable');
  await poll(detail('stale', stream({ status: 'offline' })));
  assert.equal(page.reloads, 0);
  assert.equal(warning.textContent, 'stale');
  await poll(detail('fresh', live));
  assert.equal(page.reloads, 0);
  assert.equal(warning.classList.contains('hidden'), true);
  await poll(detail('fresh', stream({ status: 'offline' })));
  assert.equal(page.reloads, 1);
  assert.deepEqual(page.requests, Array(4).fill('/api/v1/streams/id/'));
});

test('a stream page whose status was unknown reloads once a fresh source confirms it', async () => {
  const { page, poll } = streamPage(stream({ status: 'unknown' }));
  await poll(detail('unavailable', stream({ status: 'unknown' })));
  assert.equal(page.reloads, 0);
  await poll(detail('fresh', stream()));
  assert.equal(page.reloads, 1);
});

test('a renamed stream keeps playing under its new name', async () => {
  const { page, names, label, poll } = streamPage(stream(), {
    player: true, title: 'Main stage | Streams',
  });
  await poll(detail('fresh', stream({ effective_name: 'Grand final', description: 'Best of five' })));
  assert.equal(page.reloads, 0);
  assert.equal(page.title, 'Grand final | Streams');
  assert.deepEqual(names.map((name) => name.textContent), ['Grand final', 'Grand final']);
  assert.equal(label.attributes['aria-label'], 'Player: Grand final');
});

test('a stream page without a player reloads for a new description', async () => {
  const offline = stream({ status: 'offline' });
  const { page, poll } = streamPage(offline);
  await poll(detail('fresh', { ...offline, description: 'Back at 18:00' }));
  assert.equal(page.reloads, 1);
});

test('a stream page reloads when the stream is hidden or deleted', async () => {
  const { page, poll } = streamPage(stream(), { player: true });
  await poll({ ok: false, status: 404, json: async () => ({}) });
  assert.equal(page.reloads, 1);
});

test('the home page reloads for channels that come or go, and for fresh changes', async () => {
  const first = stream({ id: 'a' });
  const second = stream({ id: 'b', status: 'offline' });
  const home = () => catalogPage({ url: '/api/v1/streams/', streams: [entry(first), entry(second)] });
  let { page, poll } = home();
  await poll(list('unavailable', [{ ...first, status: 'unknown' }, { ...second, status: 'unknown' }]));
  assert.equal(page.reloads, 0);
  await poll(list('fresh', [first, { ...second, status: 'live' }]));
  assert.equal(page.reloads, 1);
  ({ page, poll } = home());
  await poll(list('stale', [first]));
  assert.equal(page.reloads, 1);
  ({ page, poll } = home());
  await poll(list('stale', [first, second, stream({ id: 'c' })]));
  assert.equal(page.reloads, 1);
  assert.deepEqual(page.requests, ['/api/v1/streams/']);
});

test('polling backs off while the API fails, and returns to its pace after an answer', async () => {
  const { page, poll } = streamPage(stream(), { player: true });
  await poll({ ok: false, status: 503 });
  assert.equal(page.timers.at(-1).delay, 20000);
  await poll({ ok: false, status: 503 });
  assert.equal(page.timers.at(-1).delay, 40000);
  await poll(detail('fresh', stream()));
  assert.equal(page.timers.at(-1).delay, 10000);
});
