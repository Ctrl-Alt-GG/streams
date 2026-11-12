const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');

function classes() {
  const values = new Set();
  return {
    add(value) { values.add(value); },
    remove(value) { values.delete(value); },
    toggle(value, enabled) { enabled ? values.add(value) : values.delete(value); },
    contains(value) { return values.has(value); },
  };
}

function playerHarness(native = true, options = {}) {
  const handlers = new Map();
  const lifecycle = new Map();
  const timers = [];
  const instances = [];
  const media = {
    autoplay: Boolean(options.video), muted: Boolean(options.video), playCount: 0,
    dataset: { source: 'https://media.example/hls/index.m3u8', statusUrl: '/api/v1/streams/id/',
      retryMessage: 'retrying', unsupportedMessage: 'unsupported', libraryMessage: 'library missing' },
    classList: classes(),
    canPlayType() { return native ? 'probably' : ''; },
    addEventListener(name, callback) { handlers.set(name, callback); },
    removeEventListener(name) { handlers.delete(name); },
    play() { this.playCount += 1; return Promise.resolve(); },
  };
  const message = { textContent: '', classList: classes() };
  const button = options.video ? null : { classList: classes(), addEventListener(name, callback) { this[name] = callback; } };
  const window = {
    setTimeout(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimeout() {},
    addEventListener(name, callback) { lifecycle.set(name, callback); },
    location: { href: "https://catalog.example/streams/id/", origin: "https://catalog.example", reload() { throw new Error('unexpected reload'); } },
    crypto: { randomUUID: () => "test-token" },
  };
  if (options.hls) {
    class FakeHls {
      static Events = { ERROR: 'error', MANIFEST_PARSED: 'manifest' };
      static ErrorTypes = { MEDIA_ERROR: 'media' };
      static isSupported() { return true; }
      constructor(configuration) { this.configuration = configuration; this.events = new Map(); instances.push(this); }
      on(name, callback) { this.events.set(name, callback); }
      loadSource(source) { this.source = source; }
      attachMedia(element) { this.media = element; }
      destroy() { this.destroyed = true; }
    }
    window.Hls = FakeHls;
  }
  const document = { querySelector(selector) {
    return { '[data-hls-player]': media, '[data-player-message]': message,
      '[data-audio-start]': button }[selector];
  } };
  vm.runInNewContext(readFileSync('src/catalog/static/catalog/js/player.js', 'utf8'), {
    document, window, AbortSignal, URL,
    fetch: async () => ({ ok: true, json: async () => ({ result: { status: 'live' } }) }),
  });
  return { handlers, lifecycle, timers, media, message, button, instances };
}

test('native HLS retries replace listeners and resume after bfcache navigation', async () => {
  const player = playerHarness();
  player.button.click();
  const firstReady = player.handlers.get('loadedmetadata');
  player.handlers.get('error')();
  assert.equal(player.timers.at(-1).delay, 2000);
  await player.timers.at(-1).callback();
  assert.equal(player.handlers.size, 2);
  assert.equal(player.handlers.get('loadedmetadata'), firstReady);
  player.handlers.get('error')();
  assert.equal(player.timers.at(-1).delay, 4000);
  player.lifecycle.get('pagehide')();
  player.handlers.clear();
  player.lifecycle.get('pageshow')({ persisted: true });
  assert.equal(player.handlers.size, 2);
});

test('missing player library explains the load failure', () => {
  const player = playerHarness(false);
  player.button.click();
  assert.equal(player.message.textContent, 'library missing');
});

test('native video loads media directly, autoplays muted, and resumes after bfcache', () => {
  const player = playerHarness(true, { video: true });
  assert.equal(player.media.src, player.media.dataset.source);
  assert.equal(player.media.muted, true);
  player.handlers.get('loadedmetadata')();
  assert.equal(player.media.playCount, 1);
  player.lifecycle.get('pagehide')();
  assert.equal(player.handlers.size, 0);
  player.lifecycle.get('pageshow')({ persisted: true });
  player.handlers.get('loadedmetadata')();
  assert.equal(player.media.playCount, 2);
});

test('bundled Hls.js video attaches media, scopes requests, retries and resumes', async () => {
  const player = playerHarness(false, { video: true, hls: true });
  assert.equal(player.instances.length, 1);
  const first = player.instances[0];
  assert.equal(first.source, player.media.dataset.source);
  assert.equal(first.media, player.media);
  first.events.get('manifest')();
  assert.equal(player.media.playCount, 1);
  let openedUrl;
  first.configuration.xhrSetup({ open(method, url) { openedUrl = new URL(url); } }, first.source);
  assert.equal(openedUrl.searchParams.get('_streams_origin'), 'https://catalog.example');
  assert.equal(openedUrl.searchParams.get('_streams_playback'), 'test-token');
  first.events.get('error')(null, { fatal: true, type: 'network' });
  assert.equal(first.destroyed, true);
  await player.timers.at(-1).callback();
  assert.equal(player.instances.length, 2);
  player.lifecycle.get('pagehide')();
  assert.equal(player.instances[1].destroyed, true);
  player.lifecycle.get('pageshow')({ persisted: true });
  assert.equal(player.instances.length, 3);
});

test('catalog freshness warnings update without interrupting playback', async () => {
  const timers = [];
  let reloads = 0;
  const warning = { classList: classes(), dataset: { staleMessage: 'stale', unavailableMessage: 'unavailable' } };
  let status = 'stale';
  let streamStatus = 'live';
  const initial = { source: 'fresh', streams: [['id', 'live', true, 'radio', '']] };
  const document = {
    hidden: false,
    getElementById() { return { textContent: JSON.stringify(initial) }; },
    querySelector(selector) { return selector === '[data-source-warning]' ? warning : { dataset: { streamId: 'id' } }; },
  };
  const window = {
    setTimeout(callback) { timers.push(callback); }, clearTimeout() {}, addEventListener() {},
    location: { reload() { reloads += 1; } },
  };
  vm.runInNewContext(readFileSync('src/catalog/static/catalog/js/catalog.js', 'utf8'), {
    document, window, AbortSignal,
    fetch: async () => ({ ok: true, json: async () => ({ source: { status }, results: [
      { id: 'id', status: streamStatus, audio_only: true, effective_name: 'radio', description: '' },
    ] }) }),
  });
  await timers.at(-1)();
  assert.equal(reloads, 0);
  assert.equal(warning.textContent, 'stale');
  status = 'fresh';
  await timers.at(-1)();
  assert.equal(warning.classList.contains('hidden'), true);
  streamStatus = 'offline';
  await timers.at(-1)();
  assert.equal(reloads, 1);
});
