function initInstagramDownloadPage() {
  const form = document.getElementById("instagram-download-form");
  const urlInput = document.getElementById("instagram-url");
  const clearButton = document.getElementById("clear-instagram-download-form");
  const downloadButton = document.getElementById("download-instagram-button");
  const notice = document.getElementById("instagram-download-notice");
  const resultContainer = document.getElementById("instagram-download-result");

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
    const parts = String(path || "").split(/[\\/]/);
    return parts[parts.length - 1] || path || "download";
  }

  function emptyState() {
    return `
      <div class="empty">
        <span class="empty-icon">↓</span>
        <span class="empty-title">Nothing downloaded yet</span>
        <span>Paste an Instagram URL on the left to get started.</span>
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
        <p class="job-message">Fetching media from Instagram. Reels usually finish in under 15 seconds. Posts, carousels, stories, and highlights may take longer.</p>
      </article>`;
    elapsedInterval = setInterval(() => {
      elapsedSeconds++;
      const el = document.getElementById("download-elapsed");
      if (el) el.textContent = elapsedSeconds + "s";
      if (elapsedSeconds === 15) setNotice(notice, "Still working — fetching the media from Instagram…");
      if (elapsedSeconds === 40) setNotice(notice, "Taking a bit longer than usual — still going, hang tight…");
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

    const downloadAll = download.files.length > 1 && download.zip_url
      ? `<a class="btn btn-primary" href="${appPath(download.zip_url)}">Download all (${download.files.length})</a>`
      : "";

    resultContainer.innerHTML = `
      <article class="job-card">
        <header class="job-header">
          <div>
            <h3 class="job-title">Instagram download complete</h3>
            <span class="job-id">${escapeHtml(download.download_id)}</span>
          </div>
          <span class="status-pill good">Ready</span>
        </header>
        <dl class="job-stats">
          <div class="wide"><dt>Output folder</dt><dd>${escapeHtml(download.output_dir)}</dd></div>
        </dl>
        ${downloadAll ? `<div class="row">${downloadAll}</div>` : ""}
        <div class="file-list">${fileRows}</div>
      </article>
    `;
  }

  async function submitDownload(event) {
    event.preventDefault();
    const url = urlInput.value.trim();
    if (!url) {
      setNotice(notice, "Please enter an Instagram URL.", "error");
      return;
    }

    downloadButton.disabled = true;
    clearButton.disabled = true;
    setNotice(notice, "Starting download…");
    renderLoading();

    try {
      const response = await fetch(appPath("/instagram/downloads"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
      });
      clearLoading();
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't download the Instagram media."));
      const body = await response.json();
      renderResult(body);
      setNotice(notice, "Download complete. Use the file links to save the results.", "success");
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
    setNotice(notice, "Enter an Instagram URL, then click Download.");
  }

  form.addEventListener("submit", submitDownload);
  clearButton.addEventListener("click", clearForm);
}
