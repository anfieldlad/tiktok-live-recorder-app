function initDownloadPage() {
  const form = document.getElementById("post-download-form");
  const urlInput = document.getElementById("post-url");
  const clearButton = document.getElementById("clear-post-download-form");
  const downloadButton = document.getElementById("download-post-button");
  const notice = document.getElementById("post-download-notice");
  const resultContainer = document.getElementById("post-download-result");

  function escapeHtml(value) {
    return String(value)
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

  function renderResult(download) {
    if (!download.files || !download.files.length) {
      resultContainer.innerHTML = '<div class="empty">The download finished, but no files were returned.</div>';
      return;
    }

    const fileRows = download.files.map((path, index) => {
      const url = download.file_urls[index];
      return `
        <div class="meta-card wide">
          <p class="meta-label">File ${index + 1}</p>
          <p class="meta-value">${escapeHtml(path)}</p>
          <div class="actions" style="margin-top:10px;">
            <a class="button-link primary" href="${appPath(url)}">Download ${escapeHtml(fileName(path))}</a>
          </div>
        </div>
      `;
    }).join("");

    resultContainer.innerHTML = `
      <article class="card">
        <div class="job-top">
          <div class="job-identity">
            <h3 class="job-name">Post download complete</h3>
            <div class="job-id">${escapeHtml(download.download_id)}</div>
          </div>
          <span class="pill status-pill good">Ready</span>
        </div>
        <div class="job-meta">
          <div class="meta-card wide">
            <p class="meta-label">Output folder</p>
            <p class="meta-value">${escapeHtml(download.output_dir)}</p>
          </div>
          ${fileRows}
        </div>
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
    setNotice(notice, "Downloading the TikTok post. This may take a moment...");

    try {
      const response = await fetch(appPath("/downloads"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
      });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't download the TikTok post."));
      const body = await response.json();
      renderResult(body);
      setNotice(notice, "Post downloaded. Use the file links to save the results.", "success");
      form.reset();
    } catch (error) {
      setNotice(notice, error.message, "error");
    } finally {
      downloadButton.disabled = false;
      clearButton.disabled = false;
    }
  }

  function clearForm() {
    form.reset();
    resultContainer.innerHTML = '<div class="empty">No post downloaded yet.</div>';
    setNotice(notice, "Enter a public TikTok post URL, then click Download.");
  }

  form.addEventListener("submit", submitDownload);
  clearButton.addEventListener("click", clearForm);
}
