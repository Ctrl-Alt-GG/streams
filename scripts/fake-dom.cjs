// Stand-ins for the parts of a browser that the scripts use, for running them in a vm context.

// An element's classList.
function classes() {
  const values = new Set();
  return {
    add(value) { values.add(value); },
    remove(value) { values.delete(value); },
    toggle(value, enabled) { enabled ? values.add(value) : values.delete(value); },
    contains(value) { return values.has(value); },
  };
}

// Timers that run when the test says so. Clearing one removes it, as in a browser.
function timers() {
  let nextId = 1;
  const pending = new Map();
  return {
    setTimeout(callback, delay = 0) {
      const id = nextId;
      nextId += 1;
      pending.set(id, { callback, delay });
      return id;
    },
    clearTimeout(id) { pending.delete(id); },
    get pending() { return [...pending.values()]; },
    // Runs the timer that was set first, and returns what its callback returns.
    runNext() {
      const [id, timer] = pending.entries().next().value ?? [];
      if (id === undefined) throw new Error('No timer is pending.');
      pending.delete(id);
      return timer.callback();
    },
  };
}

// The window's events, such as pagehide and pageshow, which the test dispatches.
function events() {
  const listeners = new Map();
  return {
    addEventListener(name, callback) {
      listeners.set(name, [...(listeners.get(name) ?? []), callback]);
    },
    dispatch(name, event = {}) {
      for (const callback of listeners.get(name) ?? []) callback(event);
    },
  };
}

// fetch(), whose requests wait for the test to answer them, and fail once their signal aborts.
function requests() {
  const waiting = [];
  const made = [];
  return {
    made,
    fetch(url, { signal } = {}) {
      made.push(url);
      return new Promise((resolve, reject) => {
        signal?.addEventListener('abort', () => reject(signal.reason));
        waiting.push(resolve);
      });
    },
    // Answers the oldest request that is still waiting.
    answer(response) {
      const resolve = waiting.shift();
      if (!resolve) throw new Error('No request is waiting.');
      resolve(response);
    },
  };
}

// Lets the promises that are ready run, such as what a script does after an answer.
const settle = () => new Promise((resolve) => setImmediate(resolve));

module.exports = { classes, events, requests, settle, timers };
