const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const vm = require('node:vm');

// Runs copy.js on a page with one copy button; `click()` presses it.
function copyPage(clipboard) {
  const timers = [];
  const selected = [];
  const target = { textContent: '  rtmps://media.example/live  ' };
  const status = { textContent: '', dataset: { message: 'Press Ctrl+C' } };
  const button = {
    textContent: 'Copy server URL',
    dataset: { copyTarget: 'publish-server', copyStatus: 'obs-copy-status', copiedMessage: 'Copied' },
    addEventListener(name, callback) { this.click = callback; },
  };
  const elements = { 'publish-server': target, 'obs-copy-status': status };
  const document = {
    querySelectorAll: () => [button],
    getElementById: (id) => elements[id] ?? null,
    createRange: () => ({ selectNodeContents(node) { this.node = node; } }),
  };
  const window = {
    setTimeout(callback, delay) { timers.push({ callback, delay }); return timers.length; },
    clearTimeout() {},
    getSelection: () => ({ removeAllRanges() {}, addRange(range) { selected.push(range.node); } }),
  };
  vm.runInNewContext(readFileSync('src/catalog/static/catalog/js/copy.js', 'utf8'), {
    document, window, navigator: { clipboard },
  });
  return { button, status, timers, selected, target };
}

test('a copy button confirms the copy, then shows its label again', async () => {
  const copied = [];
  const page = copyPage({ writeText: async (text) => { copied.push(text); } });
  await page.button.click();
  assert.deepEqual(copied, ['rtmps://media.example/live']);
  assert.equal(page.button.textContent, 'Copied');
  assert.equal(page.timers.at(-1).delay, 2000);
  page.timers.at(-1).callback();
  assert.equal(page.button.textContent, 'Copy server URL');
});

test('without clipboard access, the text is selected and the page says how to copy it', async () => {
  const page = copyPage({ writeText: async () => { throw new Error('denied'); } });
  await page.button.click();
  assert.deepEqual(page.selected, [page.target]);
  assert.equal(page.status.textContent, 'Press Ctrl+C');
  assert.equal(page.button.textContent, 'Copy server URL');
});

test('on a page without the Clipboard API, the text is selected too', async () => {
  const page = copyPage(undefined);
  await page.button.click();
  assert.equal(page.status.textContent, 'Press Ctrl+C');
});
