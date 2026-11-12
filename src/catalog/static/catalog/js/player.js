(() => {
  const media = document.querySelector("[data-hls-player]");
  const message = document.querySelector("[data-player-message]");
  const startButton = document.querySelector("[data-audio-start]");
  if (!media || !message) {
    return;
  }

  let player = null;
  let retryTimer = null;
  let playbackRequested = false;
  let retryDelay = 2000;
  let videoOnlyFallbackAttempted = false;
  let suspended = false;

  const showMessage = (text) => {
    message.textContent = text;
    message.classList.remove("hidden");
    message.classList.add("grid");
  };

  const hideMessage = () => {
    message.classList.add("hidden");
    message.classList.remove("grid");
  };

  const originScopedRequest = (playbackToken) => (xhr, url) => {
    const requestUrl = new URL(url, window.location.href);
    requestUrl.searchParams.set("_streams_origin", window.location.origin);
    if (requestUrl.pathname.endsWith("/index.m3u8")) {
      requestUrl.searchParams.set("cookieCheck", "1");
      requestUrl.searchParams.set("_streams_playback", playbackToken);
    }
    xhr.open("GET", requestUrl.href, true);
  };

  const videoOnlySource = () => {
    const level = player?.levels[player.currentLevel >= 0 ? player.currentLevel : 0];
    const url = Array.isArray(level?.url) ? level.url[level.urlId ?? 0] : level?.url;
    return typeof url === "string" ? url : null;
  };

  const handleMediaReady = () => {
    retryDelay = 2000;
    hideMessage();
    startButton?.classList.add("hidden");
    media.classList.remove("hidden");
    if (media.autoplay || playbackRequested) {
      media.play().catch(() => {});
    }
  };

  const scheduleRetry = () => {
    if (suspended) return;
    showMessage(media.dataset.retryMessage);
    window.clearTimeout(retryTimer);
    retryTimer = window.setTimeout(async () => {
      try {
        const response = await fetch(media.dataset.statusUrl, { cache: "no-store", signal: AbortSignal.timeout(5000) });
        if (response.status === 404) { window.location.reload(); return; }
        if (!response.ok) throw new Error("Status unavailable");
        const status = await response.json();
        if (suspended) return;
        if (status.result.status !== "live") { window.location.reload(); return; }
        loadStream();
      } catch {
        scheduleRetry();
      }
    }, retryDelay);
    retryDelay = Math.min(retryDelay * 2, 30000);
  };

  const loadStream = (source = media.dataset.source) => {
    if (suspended) return;
    retryTimer = null;
    media.removeEventListener("loadedmetadata", handleMediaReady);
    media.removeEventListener("error", scheduleRetry);
    player?.destroy();
    player = null;

    if (window.Hls?.isSupported()) {
      const playbackToken = window.crypto.randomUUID();
      player = new window.Hls({
        maxLiveSyncPlaybackRate: 1.5,
        xhrSetup: originScopedRequest(playbackToken),
      });
      player.on(window.Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) {
          return;
        }
        const fallbackSource =
          data.type === window.Hls.ErrorTypes.MEDIA_ERROR && !videoOnlyFallbackAttempted
            ? videoOnlySource()
            : null;
        player.destroy();
        player = null;
        if (fallbackSource) {
          videoOnlyFallbackAttempted = true;
          loadStream(fallbackSource);
          return;
        }
        scheduleRetry();
      });
      player.on(window.Hls.Events.MANIFEST_PARSED, handleMediaReady);
      player.loadSource(source);
      player.attachMedia(media);
      return;
    }

    if (media.canPlayType("application/vnd.apple.mpegurl")) {
      media.addEventListener("loadedmetadata", handleMediaReady, { once: true });
      media.addEventListener("error", scheduleRetry, { once: true });
      media.src = source;
      return;
    }

    showMessage(window.Hls ? media.dataset.unsupportedMessage : media.dataset.libraryMessage);
  };

  window.addEventListener(
    "pagehide",
    () => {
      suspended = true;
      window.clearTimeout(retryTimer);
      media.removeEventListener("loadedmetadata", handleMediaReady);
      media.removeEventListener("error", scheduleRetry);
      player?.destroy();
      player = null;
    },
  );
  window.addEventListener("pageshow", (event) => {
    suspended = false;
    if (event.persisted && (media.autoplay || playbackRequested)) loadStream();
  });

  if (startButton) {
    startButton.addEventListener(
      "click",
      () => {
        startButton.disabled = true;
        playbackRequested = true;
        loadStream();
      },
      { once: true },
    );
  } else {
    loadStream();
  }
})();
