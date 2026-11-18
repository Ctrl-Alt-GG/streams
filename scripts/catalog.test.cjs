const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const { classes, events, requests, settle, timers } = require('./fake-dom.cjs');

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
// What the page shows of a stream: the fields views.WATCHED_FIELDS names.
const fields = ['id', 'status', 'audio_only', 'effective_name', 'description'];
const shown = (item) => Object.fromEntries(fields.map((field) => [field, item[field]]));
const answer = (body) => ({ ok: true, status: 200, json: async () => body });
const detail = (source, item) => answer({ source: { status: source }, result: item });
const list = (source, items) => answer({ source: { status: source }, results: items });

// Runs catalog.js on a page showing `streams`. `poll(response)` runs one status check.
function catalogPage({ url, streams, streamPage = false, player = false, title = 'Streams' }) {
  const page = { reloads: 0, title };
  const clock = timers();
  const lifecycle = events();
  const network = requests();
  const warning = element({ staleMessage: 'stale', unavailableMessage: 'unavailable' });
  const names = [element(), element()];
  const label = element({ streamNameLabel: 'Player' });
  const document = {
    hidden: false,
    get title() { return page.title; },
    set title(value) { page.title = value; },
    getElementById(id) {
      return id === 'catalog-watch-state'
        ? { textContent: JSON.stringify({ url, fields, streams: streams.map(shown) }) }
        : null;
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
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    addEventListener: lifecycle.addEventListener,
    location: { reload() { page.reloads += 1; } },
  };
  vm.runInNewContext(script, {
    document, window, AbortController, AbortSignal, fetch: network.fetch,
  });

  // Starts the next check, and returns a function that answers it.
  const startPoll = () => {
    const running = clock.runNext();
    return async (response) => {
      network.answer(response);
      await running;
      await settle();
    };
  };
  return {
    page, warning, names, label, clock, lifecycle, requests: network.made, startPoll,
    async poll(response) { await startPoll()(response); },
  };
}

const streamPage = (item, options = {}) => catalogPage({
  url: '/api/v1/streams/id/', streams: [item], streamPage: true, ...options,
});

test('a live stream keeps playing while the status source is unavailable or stale', async () => {
  const live = stream();
  const { page, warning, poll, requests: made } = streamPage(live, { player: true });
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
  assert.deepEqual(made, Array(4).fill('/api/v1/streams/id/'));
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
  const { page, poll, clock } = streamPage(stream(), { player: true });
  await poll({ ok: false, status: 404, json: async () => ({}) });
  assert.equal(page.reloads, 1);
  assert.equal(clock.pending.length, 0);
});

test('the home page reloads for channels that come or go, and for fresh changes', async () => {
  const first = stream({ id: 'a' });
  const second = stream({ id: 'b', status: 'offline' });
  const home = () => catalogPage({ url: '/api/v1/streams/', streams: [first, second] });
  let { page, poll } = home();
  await poll(list('unavailable', [{ ...first, status: 'unknown' }, { ...second, status: 'unknown' }]));
  assert.equal(page.reloads, 0);
  await poll(list('fresh', [first, { ...second, status: 'live' }]));
  assert.equal(page.reloads, 1);
  ({ page, poll } = home());
  await poll(list('stale', [first]));
  assert.equal(page.reloads, 1);
  let made;
  ({ page, poll, requests: made } = home());
  await poll(list('stale', [first, second, stream({ id: 'c' })]));
  assert.equal(page.reloads, 1);
  assert.deepEqual(made, ['/api/v1/streams/']);
});

test('polling backs off while the API fails, and returns to its pace after an answer', async () => {
  const { page, poll, clock } = streamPage(stream(), { player: true });
  await poll({ ok: false, status: 503 });
  assert.equal(clock.pending.at(-1).delay, 20000);
  await poll({ ok: false, status: 503 });
  assert.equal(clock.pending.at(-1).delay, 40000);
  await poll(detail('fresh', stream()));
  assert.equal(clock.pending.at(-1).delay, 10000);
  assert.equal(page.reloads, 0);
});

test('an answer that is not the API\'s is waited out like an outage', async () => {
  const { page, poll, clock } = catalogPage({ url: '/api/v1/streams/', streams: [stream()] });
  await poll(answer({ detail: 'Service unavailable' }));
  assert.equal(page.reloads, 0);
  assert.equal(clock.pending.at(-1).delay, 20000);
});

test('an answer that arrives after the page was left changes nothing', async () => {
  const { page, clock, lifecycle, startPoll, names } = streamPage(stream(), { player: true });
  const answerLate = startPoll();
  lifecycle.dispatch('pagehide');
  await answerLate(detail('fresh', stream({ status: 'offline', effective_name: 'Other' })));
  assert.equal(page.reloads, 0);
  assert.equal(names[0].textContent, '');
  assert.equal(clock.pending.length, 0);
});

test('an answer that is read after the page was left changes nothing', async () => {
  const { page, clock, lifecycle, startPoll } = streamPage(stream(), { player: true });
  let readBody;
  const body = new Promise((resolve) => { readBody = resolve; });
  const answerNow = startPoll();
  const answered = answerNow({ ok: true, status: 200, json: () => body });
  await settle();
  lifecycle.dispatch('pagehide');
  readBody({ source: { status: 'fresh' }, result: stream({ status: 'offline' }) });
  await answered;
  assert.equal(page.reloads, 0);
  assert.equal(clock.pending.length, 0);
});

test('coming back from the back/forward cache runs exactly one polling loop', async () => {
  const { page, clock, lifecycle, startPoll, requests: made } = streamPage(stream(), { player: true });
  const answerBeforeLeaving = startPoll();
  lifecycle.dispatch('pagehide');
  lifecycle.dispatch('pageshow', { persisted: true });
  assert.equal(clock.pending.length, 1);
  assert.equal(clock.pending[0].delay, 0);
  const answerRestored = startPoll();
  await answerBeforeLeaving(detail('fresh', stream({ status: 'offline' })));
  assert.equal(page.reloads, 0);
  await answerRestored(detail('fresh', stream()));
  assert.equal(page.reloads, 0);
  assert.equal(clock.pending.length, 1);
  assert.equal(clock.pending[0].delay, 10000);
  assert.equal(made.length, 2);
});
