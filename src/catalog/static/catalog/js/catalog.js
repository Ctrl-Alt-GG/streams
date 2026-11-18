(() => {
  const stateNode = document.getElementById("catalog-watch-state");
  if (!stateNode) return;
  const initial = JSON.parse(stateNode.textContent);
  const onStreamPage = Boolean(document.querySelector("[data-stream-id]"));
  const hasPlayer = Boolean(document.querySelector("[data-hls-player]"));
  const pollMs = 10000;
  const maxDelayMs = 60000;
  const requestTimeoutMs = 5000;

  // A status counts once a fresh source confirms it. While the source is stale or unavailable,
  // for example during a Redis restart, streams keep their last known status or read "unknown",
  // which says nothing about whether they are still live.

  // The fields of a stream that the page shows, which the server names as the API does.
  const summary = (stream) => JSON.stringify(initial.fields.map((field) => stream[field]));
  const shown = new Map(initial.streams.map((stream) => [stream.id, summary(stream)]));

  // The home page reloads when a channel appears or goes away, and for any change a fresh source
  // confirms.
  const listChanged = (streams, fresh) =>
    streams.length !== shown.size
    || streams.some((stream) => !shown.has(stream.id))
    || (fresh && streams.some((stream) => summary(stream) !== shown.get(stream.id)));

  const shownStream = initial.streams[0] ?? {};
  let name = shownStream.effective_name;
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
    if (
      fresh
      && (stream.status !== shownStream.status || stream.audio_only !== shownStream.audio_only)
    ) {
      return true;
    }
    if (stream.effective_name !== name) rename(stream.effective_name);
    return !hasPlayer && stream.description !== shownStream.description;
  };

  // Whether an answer is the API's. Anything else is treated like an outage, rather than read as
  // streams that went away.
  const isCatalog = (body) =>
    typeof body?.source?.status === "string"
    && (onStreamPage
      ? typeof body.result === "object" && body.result !== null
      : Array.isArray(body.results));

  // Each start of polling, when the page loads and when it comes back from the back/forward
  // cache, owns its timer and its request. Leaving the page stops it: its request is cancelled,
  // and an answer that still arrives changes nothing.
  let current = null;

  const stop = () => {
    if (!current) return;
    current.controller.abort();
    window.clearTimeout(current.timer);
    current = null;
  };

  const start = (firstDelayMs) => {
    stop();
    const run = { controller: new AbortController(), delay: pollMs, timer: null };
    current = run;

    const poll = async () => {
      if (current !== run) return;
      try {
        if (!document.hidden) {
          const signal = AbortSignal.any([
            run.controller.signal,
            AbortSignal.timeout(requestTimeoutMs),
          ]);
          const response = await fetch(initial.url, { cache: "no-store", signal });
          if (current !== run) return;
          // The stream was hidden or deleted: the reloaded page says so.
          if (onStreamPage && response.status === 404) {
            stop();
            window.location.reload();
            return;
          }
          if (!response.ok) throw new Error("Catalog unavailable");
          const catalog = await response.json();
          if (current !== run) return;
          if (!isCatalog(catalog)) throw new Error("Unexpected catalog answer");
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
            stop();
            window.location.reload();
            return;
          }
        }
        run.delay = pollMs;
      } catch {
        if (current !== run) return;
        run.delay = Math.min(run.delay * 2, maxDelayMs);
      }
      run.timer = window.setTimeout(poll, run.delay);
    };

    run.timer = window.setTimeout(poll, firstDelayMs);
  };

  window.addEventListener("pagehide", stop);
  window.addEventListener("pageshow", (event) => {
    // Back from the back/forward cache: check at once, as anything may have changed meanwhile.
    if (event.persisted) start(0);
  });
  start(pollMs);
})();
