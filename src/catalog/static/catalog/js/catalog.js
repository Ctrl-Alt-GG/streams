(() => {
  const stateNode = document.getElementById("catalog-watch-state");
  if (!stateNode) return;
  const initial = JSON.parse(stateNode.textContent);
  const onStreamPage = Boolean(document.querySelector("[data-stream-id]"));
  const hasPlayer = Boolean(document.querySelector("[data-hls-player]"));
  let delay = 10000;
  let timer;
  let stopped = false;

  // A status counts once a fresh source confirms it. While the source is stale or unavailable,
  // for example during a Redis restart, streams keep their last known status or read "unknown",
  // which says nothing about whether they are still live.

  const summary = (stream) => JSON.stringify([
    stream.id, stream.status, stream.audio_only, stream.effective_name, stream.description,
  ]);
  const shown = new Map(initial.streams.map((entry) => [entry[0], JSON.stringify(entry)]));

  // The home page reloads when a channel appears or goes away, and for any change a fresh source
  // confirms.
  const listChanged = (streams, fresh) =>
    streams.length !== shown.size
    || streams.some((stream) => !shown.has(stream.id))
    || (fresh && streams.some((stream) => summary(stream) !== shown.get(stream.id)));

  const [, shownStatus, shownAudioOnly, shownName, shownDescription] = initial.streams[0] ?? [];
  let name = shownName;
  const rename = (newName) => {
    document.title = document.title.replace(name, newName);
    document.querySelectorAll("[data-stream-name]").forEach((element) => {
      element.textContent = newName;
    });
    document.querySelectorAll("[data-stream-name-label]").forEach((element) => {
      element.setAttribute("aria-label", `${element.dataset.streamNameLabel}: ${newName}`);
    });
    name = newName;
  };

  // A stream's page reloads for a status change a fresh source confirms, so a player keeps running
  // through a blip of the status source. A new name is shown in place. A new description, which
  // the server renders from Markdown, waits for the next visit while a player is on the page.
  const streamChanged = (stream, fresh) => {
    if (fresh && (stream.status !== shownStatus || stream.audio_only !== shownAudioOnly)) {
      return true;
    }
    if (stream.effective_name !== name) rename(stream.effective_name);
    return !hasPlayer && stream.description !== shownDescription;
  };

  const poll = async () => {
    if (stopped) return;
    try {
      if (!document.hidden) {
        const response = await fetch(initial.url, { cache: "no-store", signal: AbortSignal.timeout(5000) });
        // The stream was hidden or deleted: the reloaded page says so.
        if (onStreamPage && response.status === 404) {
          window.location.reload();
          return;
        }
        if (!response.ok) throw new Error("Catalog unavailable");
        const catalog = await response.json();
        const fresh = catalog.source.status === "fresh";
        const warning = document.querySelector("[data-source-warning]");
        if (warning) {
          warning.classList.toggle("hidden", fresh);
          warning.textContent = catalog.source.status === "stale"
            ? warning.dataset.staleMessage : warning.dataset.unavailableMessage;
        }
        const changed = onStreamPage
          ? streamChanged(catalog.result, fresh)
          : listChanged(catalog.results, fresh);
        if (changed) {
          window.location.reload();
          return;
        }
      }
      delay = 10000;
    } catch {
      delay = Math.min(delay * 2, 60000);
    }
    timer = window.setTimeout(poll, delay);
  };
  window.addEventListener("pagehide", () => { stopped = true; window.clearTimeout(timer); });
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { stopped = false; poll(); }
  });
  timer = window.setTimeout(poll, delay);
})();
