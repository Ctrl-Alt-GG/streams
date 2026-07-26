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

  const showMessage = (text) => {
    message.textContent = text;
    message.classList.remove("hidden");
    message.classList.add("grid");
  };

  const hideMessage = () => {
    message.classList.add("hidden");
    message.classList.remove("grid");
  };

  const handleMediaReady = () => {
    hideMessage();
    startButton?.classList.add("hidden");
    media.classList.remove("hidden");
    if (media.autoplay || playbackRequested) {
      media.play().catch(() => {});
    }
  };

  const scheduleRetry = () => {
    showMessage(media.dataset.retryMessage);
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
      player.on(window.Hls.Events.MANIFEST_PARSED, handleMediaReady);
      player.loadSource(media.dataset.source);
      player.attachMedia(media);
      return;
    }

    if (media.canPlayType("application/vnd.apple.mpegurl")) {
      media.src = media.dataset.source;
      media.addEventListener("loadedmetadata", handleMediaReady, { once: true });
      media.addEventListener("error", scheduleRetry, { once: true });
      return;
    }

    showMessage(media.dataset.unsupportedMessage);
  };

  window.addEventListener(
    "pagehide",
    () => {
      window.clearTimeout(retryTimer);
      player?.destroy();
    },
    { once: true },
  );

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
