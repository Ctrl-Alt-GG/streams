// A stand-in for an element's classList, for scripts run in a vm context.
function classes() {
  const values = new Set();
  return {
    add(value) { values.add(value); },
    remove(value) { values.delete(value); },
    toggle(value, enabled) { enabled ? values.add(value) : values.delete(value); },
    contains(value) { return values.has(value); },
  };
}

module.exports = { classes };
