const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');
const { classes, events, timers } = require('./fake-dom.cjs');

function playerHarness(native = true, options = {}) {
  const handlers = new Map();
  const lifecycle = events();
  const clock = timers();
  const instances = [];
  const page = { reloads: 0 };
  const media = {
    autoplay: Boolean(options.video), muted: Boolean(options.video), playCount: 0,
    dataset: { source: 'https://media.example/hls/index.m3u8',
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
    setTimeout: clock.setTimeout,
    clearTimeout: clock.clearTimeout,
    addEventListener: lifecycle.addEventListener,
    location: { href: 'https://catalog.example/streams/id/', origin: 'https://catalog.example', reload() { page.reloads += 1; } },
    crypto: { randomUUID: () => 'test-token' },
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
    document, window, URL,
    fetch: () => { throw new Error('The player asks no API.'); },
  });
  return { handlers, lifecycle, clock, media, message, button, instances, page };
}

test('native HLS retries replace listeners and resume after bfcache navigation', () => {
  const player = playerHarness();
  player.button.click();
  const firstReady = player.handlers.get('loadedmetadata');
  player.handlers.get('error')();
  assert.equal(player.clock.pending.at(-1).delay, 2000);
  player.clock.runNext();
  assert.equal(player.handlers.size, 2);
  assert.equal(player.handlers.get('loadedmetadata'), firstReady);
  player.handlers.get('error')();
  assert.equal(player.clock.pending.at(-1).delay, 4000);
  player.lifecycle.dispatch('pagehide');
  assert.equal(player.clock.pending.length, 0);
  player.handlers.clear();
  player.lifecycle.dispatch('pageshow', { persisted: true });
  assert.equal(player.handlers.size, 2);
});

test('audio that failed plays on once the stream is back, without reloading the page', () => {
  const player = playerHarness();
  player.button.click();
  player.handlers.get('loadedmetadata')();
  assert.equal(player.media.playCount, 1);
  player.handlers.get('error')();
  assert.equal(player.message.textContent, 'retrying');
  player.clock.runNext();
  player.handlers.get('loadedmetadata')();
  assert.equal(player.media.playCount, 2);
  assert.equal(player.message.classList.contains('hidden'), true);
  assert.equal(player.page.reloads, 0);
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
  player.lifecycle.dispatch('pagehide');
  assert.equal(player.handlers.size, 0);
  player.lifecycle.dispatch('pageshow', { persisted: true });
  player.handlers.get('loadedmetadata')();
  assert.equal(player.media.playCount, 2);
});

test('bundled Hls.js video attaches media, scopes requests, retries and resumes', () => {
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
  player.clock.runNext();
  assert.equal(player.instances.length, 2);
  player.lifecycle.dispatch('pagehide');
  assert.equal(player.instances[1].destroyed, true);
  player.lifecycle.dispatch('pageshow', { persisted: true });
  assert.equal(player.instances.length, 3);
  assert.equal(player.page.reloads, 0);
});

test('leaving the page cancels a retry that is waiting', () => {
  const player = playerHarness(false, { video: true, hls: true });
  player.instances[0].events.get('error')(null, { fatal: true, type: 'network' });
  assert.equal(player.clock.pending.length, 1);
  player.lifecycle.dispatch('pagehide');
  assert.equal(player.clock.pending.length, 0);
  assert.equal(player.instances.length, 1);
});
