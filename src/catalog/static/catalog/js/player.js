(() => {
  const video = document.querySelector("[data-hls-player]");
  const message = document.querySelector("[data-player-message]");
  if (!video || !message) {
    return;
  }

  let player = null;
  let retryTimer = null;

  const showMessage = (text) => {
    message.textContent = text;
    message.classList.remove("hidden");
    message.classList.add("grid");
  };

  const hideMessage = () => {
    message.classList.add("hidden");
    message.classList.remove("grid");
  };

  const startPlayback = () => {
    hideMessage();
    video.play().catch(() => {});
  };

  const scheduleRetry = () => {
    showMessage(video.dataset.retryMessage);
    window.clearTimeout(retryTimer);
    retryTimer = window.setTimeout(loadStream, 2000);
  };

  const loadStream = () => {
    retryTimer = null;

    if (window.Hls?.isSupported()) {
      player = new window.Hls({ maxLiveSyncPlaybackRate: 1.5 });
      player.on(window.Hls.Events.ERROR, (_event, data) => {
        if (!data.fatal) {
          return;
        }
        player.destroy();
        player = null;
        scheduleRetry();
      });
      player.on(window.Hls.Events.MANIFEST_PARSED, startPlayback);
      player.loadSource(video.dataset.source);
      player.attachMedia(video);
      return;
    }

    if (video.canPlayType("application/vnd.apple.mpegurl")) {
      video.src = video.dataset.source;
      video.addEventListener("loadedmetadata", startPlayback, { once: true });
      video.addEventListener("error", scheduleRetry, { once: true });
      return;
    }

    showMessage(video.dataset.unsupportedMessage);
  };

  window.addEventListener(
    "pagehide",
    () => {
      window.clearTimeout(retryTimer);
      player?.destroy();
    },
    { once: true },
  );

  loadStream();
})();
