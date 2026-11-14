(() => {
  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    const label = button.textContent;
    const status = document.getElementById(button.dataset.copyStatus);
    let timer;
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (!target) return;
      window.clearTimeout(timer);
      try {
        await navigator.clipboard.writeText(target.textContent.trim());
        button.textContent = button.dataset.copiedMessage;
        if (status) status.textContent = "";
        timer = window.setTimeout(() => { button.textContent = label; }, 2000);
      } catch {
        // Without clipboard access, for example on a page served over plain HTTP, the text is
        // selected for the visitor to copy.
        const selection = window.getSelection();
        const range = document.createRange();
        range.selectNodeContents(target);
        selection.removeAllRanges();
        selection.addRange(range);
        if (status) status.textContent = status.dataset.message;
      }
    });
  });
})();
