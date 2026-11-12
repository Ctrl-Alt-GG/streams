(() => {
  const stateNode = document.getElementById("catalog-watch-state");
  if (!stateNode) return;
  const initial = JSON.parse(stateNode.textContent);
  const detailId = document.querySelector("[data-stream-id]")?.dataset.streamId;
  let delay = 10000;
  let timer;
  let stopped = false;
  const poll = async () => {
    if (stopped) return;
    try {
      if (!document.hidden) {
        const response = await fetch("/api/v1/streams/", { cache: "no-store", signal: AbortSignal.timeout(5000) });
        if (!response.ok) throw new Error("Catalog unavailable");
        const catalog = await response.json();
        const state = {
          source: catalog.source.status,
          streams: catalog.results
            .filter((stream) => !detailId || stream.id === detailId)
            .sort((a, b) => a.id.localeCompare(b.id))
            .map((stream) => [stream.id, stream.status, stream.audio_only, stream.effective_name, stream.description]),
        };
        const warning = document.querySelector("[data-source-warning]");
        if (warning) {
          warning.classList.toggle("hidden", state.source === "fresh");
          warning.textContent = state.source === "stale"
            ? warning.dataset.staleMessage : warning.dataset.unavailableMessage;
        }
        if (JSON.stringify(state.streams) !== JSON.stringify(initial.streams)) {
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
