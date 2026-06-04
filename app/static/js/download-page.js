function initDownloadPage() {
  const form = document.getElementById("post-download-form");
  const urlInput = document.getElementById("post-url");
  const clearButton = document.getElementById("clear-post-download-form");
  const downloadButton = document.getElementById("download-post-button");
  const notice = document.getElementById("post-download-notice");
  const resultContainer = document.getElementById("post-download-result");

  let elapsedInterval = null;
  let elapsedSeconds = 0;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function fileName(path) {
    const parts = String(path || "").split("/");
    return parts[parts.length - 1] || path || "download";
  }

  function emptyState() {
    return `
      <div class="empty">
        <span class="empty-icon">↓</span>
        <span class="empty-title">No post downloaded yet</span>
        <span>Paste a TikTok post URL on the left to get started.</span>
      </div>`;
  }

  function renderLoading() {
    elapsedSeconds = 0;
    resultContainer.innerHTML = `
      <article class="job-card">
        <header class="job-header">
          <div>
            <h3 class="job-title">Downloading…</h3>
            <span class="elapsed-counter" id="download-elapsed">0s</span>
          </div>
          <span class="status-pill live">Working</span>
        </header>
        <p class="job-message">Fetching media from TikTok. This usually takes a few seconds.</p>
      </article>`;
    elapsedInterval = setInterval(() => {
      elapsedSeconds++;
      const el = document.getElementById("download-elapsed");
      if (el) el.textContent = elapsedSeconds + "s";
      if (elapsedSeconds === 10) setNotice(notice, "Still working — fetching the media from TikTok…");
      if (elapsedSeconds === 30) setNotice(notice, "Taking a bit longer than usual — still going, hang tight…");
    }, 1000);
  }

  function clearLoading() {
    if (elapsedInterval) { clearInterval(elapsedInterval); elapsedInterval = null; }
  }

  function renderResult(download) {
    if (!download.files || !download.files.length) {
      resultContainer.innerHTML = `
        <article class="job-card">
          <header class="job-header">
            <div>
              <h3 class="job-title">Download finished</h3>
              <span class="job-id">${escapeHtml(download.download_id)}</span>
            </div>
            <span class="status-pill soft">No files</span>
          </header>
          <p class="job-message">The download finished, but no files were returned.</p>
        </article>`;
      return;
    }

    const fileRows = download.files.map((path, index) => {
      const url = download.file_urls[index];
      return `
        <div class="file-row">
          <div class="file-meta">
            <span class="file-name">${escapeHtml(fileName(path))}</span>
            <span class="file-path">${escapeHtml(path)}</span>
          </div>
          <a class="btn btn-primary" href="${appPath(url)}">Save</a>
        </div>`;
    }).join("");

    resultContainer.innerHTML = `
      <article class="job-card">
        <header class="job-header">
          <div>
            <h3 class="job-title">Post download complete</h3>
            <span class="job-id">${escapeHtml(download.download_id)}</span>
          </div>
          <span class="status-pill good">Ready</span>
        </header>
        <dl class="job-stats">
          <div class="wide"><dt>Output folder</dt><dd>${escapeHtml(download.output_dir)}</dd></div>
        </dl>
        <div class="file-list">${fileRows}</div>
      </article>
    `;
  }

  async function submitDownload(event) {
    event.preventDefault();
    const url = urlInput.value.trim();
    if (!url) {
      setNotice(notice, "Please enter a TikTok post URL.", "error");
      return;
    }

    downloadButton.disabled = true;
    clearButton.disabled = true;
    setNotice(notice, "Starting download…");
    renderLoading();

    try {
      const response = await fetch(appPath("/downloads"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
      });
      clearLoading();
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't download the TikTok post."));
      const body = await response.json();
      renderResult(body);
      setNotice(notice, "Post downloaded. Use the file links to save the results.", "success");
      form.reset();
    } catch (error) {
      clearLoading();
      resultContainer.innerHTML = emptyState();
      setNotice(notice, error.message, "error");
    } finally {
      downloadButton.disabled = false;
      clearButton.disabled = false;
    }
  }

  function clearForm() {
    clearLoading();
    form.reset();
    resultContainer.innerHTML = emptyState();
    setNotice(notice, "Enter a public TikTok post URL, then click Download.");
  }

  form.addEventListener("submit", submitDownload);
  clearButton.addEventListener("click", clearForm);
}
