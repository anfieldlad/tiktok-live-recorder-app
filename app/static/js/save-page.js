/**
 * One page for both platforms. The endpoint is chosen from the link's
 * hostname — the same rule the backend validators and the Android UrlRouter
 * already use — so the person pasting a link never has to know which app
 * they are "in".
 */
function initSavePage() {
  const form = document.getElementById("post-download-form");
  const urlInput = document.getElementById("post-url");
  const notice = document.getElementById("post-download-notice");
  const resultContainer = document.getElementById("post-download-result");
  const submitButton = document.getElementById("download-post-button");
  const clearButton = document.getElementById("clear-post-download-form");
  let elapsedInterval = null;
  let elapsedSeconds = 0;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function hostOf(rawUrl) {
    const trimmed = rawUrl.trim();
    // People paste "www.tiktok.com/@a/video/1" as often as the full link, and
    // the old page accepted it. new URL() needs a scheme, so assume https.
    for (const candidate of [trimmed, `https://${trimmed}`]) {
      try { return new URL(candidate).hostname.toLowerCase(); } catch { /* try next */ }
    }
    return null;
  }

  function platformFor(rawUrl) {
    const host = hostOf(rawUrl);
    if (!host) return null;
    if (host === "tiktok.com" || host.endsWith(".tiktok.com")) return "tiktok";
    if (host === "instagram.com" || host.endsWith(".instagram.com") || host === "instagr.am") return "instagram";
    return null;
  }

  const endpointFor = (platform) => (platform === "instagram" ? "/instagram/downloads" : "/downloads");
  const fileName = (path) => String(path ?? "").split(/[\\/]/).pop() || "download";

  function emptyState() {
    return `<div class="empty"><span class="empty-title">Nothing filed yet</span>
      <span>Paste a TikTok or Instagram link above.</span></div>`;
  }

  function clearLoading() {
    if (elapsedInterval) { clearInterval(elapsedInterval); elapsedInterval = null; }
  }

  function renderLoading(platform) {
    clearLoading();
    elapsedSeconds = 0;
    resultContainer.innerHTML = `
      <article class="job-card live" data-series="${platform === "instagram" ? "ig" : "tt"}">
        <div class="job-header">
          <div>
            <span class="job-id">Fetching · ${escapeHtml(platform)}</span>
            <h3 class="job-title">Working…</h3>
          </div>
          <span class="stamp soft">Pending</span>
        </div>
        <p class="job-message">Asking ${escapeHtml(platform)} for the media.
          <span class="elapsed-counter" id="download-elapsed">0s</span></p>
      </article>`;
    elapsedInterval = setInterval(() => {
      elapsedSeconds += 1;
      const el = document.getElementById("download-elapsed");
      if (el) el.textContent = `${elapsedSeconds}s`;
    }, 1000);
  }

  function renderResult(download, platform) {
    const series = platform === "instagram" ? "ig" : "tt";
    const files = download.files || [];
    const fileUrls = download.file_urls || [];
    const rows = files.map((path, index) => `
      <div class="file-row">
        <div class="file-meta">
          <span class="file-name">${escapeHtml(fileName(path))}</span>
          <span class="file-path">${escapeHtml(path)}</span>
        </div>
        <a class="btn btn-sm" href="${appPath(fileUrls[index])}">Take a copy</a>
      </div>`).join("");

    const zip = download.zip_url
      ? `<a class="btn btn-sm btn-quiet" href="${appPath(download.zip_url)}">Take all as zip</a>` : "";

    resultContainer.innerHTML = `
      <article class="job-card" data-series="${series}">
        <div class="job-header">
          <div>
            <span class="job-id">No. ${escapeHtml(download.download_id)} · ${escapeHtml(platform)}</span>
            <h3 class="job-title">${escapeHtml(files.length)} file${files.length === 1 ? "" : "s"} filed</h3>
          </div>
          <span class="stamp good">Filed</span>
        </div>
        <div class="file-list">${rows}</div>
        <div class="job-actions">${zip}
          <button class="btn btn-sm btn-danger" data-action="delete-download"
                  data-id="${escapeHtml(download.download_id)}" data-platform="${escapeHtml(platform)}">Discard</button>
        </div>
      </article>`;
  }

  async function submitDownload(event) {
    event.preventDefault();
    const url = urlInput.value.trim();
    const platform = platformFor(url);
    if (!platform) {
      setNotice(notice, "That does not look like a TikTok or Instagram link.", "error");
      return;
    }
    submitButton.disabled = true;
    setNotice(notice, `Fetching from ${platform}…`);
    renderLoading(platform);
    try {
      const response = await fetch(appPath(endpointFor(platform)), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!response.ok) throw new Error(await readApiError(response, "The download failed."));
      const download = await response.json();
      clearLoading();
      renderResult(download, platform);
      setNotice(notice, "Post filed.");
    } catch (error) {
      clearLoading();
      resultContainer.innerHTML = emptyState();
      setNotice(notice, error.message, "error");
    } finally {
      submitButton.disabled = false;
    }
  }

  resultContainer.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action='delete-download']");
    if (!button) return;
    const base = button.dataset.platform === "instagram" ? "/instagram/downloads" : "/downloads";
    try {
      const response = await fetch(appPath(`${base}/${button.dataset.id}`), { method: "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "Could not discard it."));
      resultContainer.innerHTML = emptyState();
      setNotice(notice, "Discarded from the server.");
    } catch (error) {
      setNotice(notice, error.message, "error");
    }
  });

  form.addEventListener("submit", submitDownload);
  clearButton.addEventListener("click", () => {
    clearLoading();
    form.reset(); resultContainer.innerHTML = emptyState();
    setNotice(notice, "Cleared.");
  });
  resultContainer.innerHTML = emptyState();
}
