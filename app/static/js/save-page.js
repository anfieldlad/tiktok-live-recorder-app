/**
 * One page for both platforms, and one register for both.
 *
 * The endpoint is chosen from the link's hostname — the same rule the backend
 * validators and the Android UrlRouter already use — so the person pasting a
 * link never has to know which app they are "in".
 *
 * Submissions stack. The old page disabled the button for the length of a
 * download and kept one result slot, which is where "downloads run one at a
 * time" came from: the server never serialized anything.
 */
function initSavePage() {
  const form = document.getElementById("post-download-form");
  const urlInput = document.getElementById("post-url");
  const notice = document.getElementById("post-download-notice");
  const resultContainer = document.getElementById("post-download-result");
  const clearButton = document.getElementById("clear-post-download-form");

  const ACTIVE_POLL_MS = 2000;
  const IDLE_POLL_MS = 10000;
  const POLL_FAILURES_BEFORE_WARNING = 3;

  let pollTimer = null;
  let currentPollMs = null;
  let consecutivePollFailures = 0;
  let pollWarningShown = false;

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

  const basePathFor = (platform) => (platform === "instagram" ? "/instagram/downloads" : "/downloads");
  const fileName = (path) => String(path ?? "").split(/[\\/]/).pop() || "download";
  // Ids are "<timestamp>-<hex>"; the hex is the distinguishing part and
  // reads as a register number. The whole id is a mouthful on a card.
  const registerNumber = (id) => String(id ?? "").split("-").pop().toUpperCase();
  const isInstagram = (entry) => entry.platform === "instagram";
  const isActive = (entry) => entry.status === "queued" || entry.status === "running";

  function stampFor(entry) {
    if (entry.status === "queued") return { label: "Queued", cls: "soft" };
    if (entry.status === "running") return { label: "Working", cls: "soft" };
    if (entry.status === "failed") return { label: "Failed", cls: "bad" };
    return { label: "Filed", cls: "good" };
  }

  // The card's job is to answer "which post is this?", not "what is the
  // server doing?" — the stamp already says that. So the title is the link the
  // user pasted, stripped of the noise they did not type.
  function titleFor(entry) {
    const url = String(entry.url ?? "").replace(/^https?:\/\//, "").replace(/^www\./, "");
    if (!url) return "—";
    return url.length > 48 ? `${url.slice(0, 47)}…` : url;
  }

  function retentionNote(entry) {
    if (!entry.fetched_at) return "";
    const removesAt = new Date(new Date(entry.fetched_at).getTime() + 24 * 3600 * 1000);
    const hoursLeft = Math.max(0, Math.round((removesAt - Date.now()) / 3600000));
    return `Taken — removed in ~${hoursLeft}h`;
  }

  function emptyState() {
    return `<div class="empty"><span class="empty-title">Nothing filed yet</span>
      <span>Paste a link above.</span></div>`;
  }

  function renderCard(entry) {
    const stamp = stampFor(entry);
    const files = entry.files || [];
    const fileUrls = entry.file_urls || [];
    const rows = files.map((path, index) => `
      <div class="file-row">
        <div class="file-meta">
          <span class="file-name">${escapeHtml(fileName(path))}</span>
          <span class="file-path">${escapeHtml(path)}</span>
        </div>
        <a class="btn btn-sm" href="${appPath(fileUrls[index])}">Take a copy</a>
      </div>`).join("");

    const zip = entry.zip_url
      ? `<a class="btn btn-sm btn-quiet" href="${appPath(entry.zip_url)}">Take all as zip</a>` : "";
    // Only a failure needs words. Queued and working are what the stamp says,
    // and how many downloads run at once is not the reader's problem.
    const message = entry.status === "failed"
      ? `<p class="job-message">${escapeHtml(entry.error || "Could not be saved.")}</p>`
      : "";
    const note = retentionNote(entry);

    return `
      <article class="job-card${isActive(entry) ? " live" : ""}" data-series="${isInstagram(entry) ? "ig" : "tt"}">
        <div class="job-header">
          <div>
            <span class="job-id">No. ${escapeHtml(registerNumber(entry.id))} · ${escapeHtml(isInstagram(entry) ? "instagram" : "tiktok")}</span>
            <h3 class="job-title">${escapeHtml(titleFor(entry))}</h3>
          </div>
          <span class="stamp ${stamp.cls}">${escapeHtml(stamp.label)}</span>
        </div>
        ${message}
        ${rows ? `<div class="file-list">${rows}</div>` : ""}
        ${note ? `<p class="retention">${escapeHtml(note)}</p>` : ""}
        <div class="job-actions">${zip}
          <button class="btn btn-sm btn-danger" data-action="delete-download"
                  data-id="${escapeHtml(entry.id)}"
                  data-platform="${escapeHtml(isInstagram(entry) ? "instagram" : "tiktok")}">Discard</button>
        </div>
      </article>`;
  }

  function render(entries) {
    resultContainer.innerHTML = entries.length
      ? entries.map(renderCard).join("")
      : emptyState();
    setPollRate(entries.some(isActive) ? ACTIVE_POLL_MS : IDLE_POLL_MS);
  }

  // A background poll must not clobber the notice describing what the user just
  // did: a single dropped request used to replace a real message with the
  // browser's raw "Failed to fetch".
  function onPollSuccess() {
    consecutivePollFailures = 0;
    if (pollWarningShown) {
      pollWarningShown = false;
      setNotice(notice, "Reconnected.");
    }
  }

  function onPollFailure() {
    consecutivePollFailures += 1;
    if (consecutivePollFailures < POLL_FAILURES_BEFORE_WARNING) return;
    pollWarningShown = true;
    setNotice(notice, "Lost connection — retrying…", "error");
  }

  async function fetchDownloads() {
    const response = await fetch(appPath("/downloads"));
    if (!response.ok) throw new Error(`Could not load the register: ${response.status}`);
    render(await response.json());
  }

  function pollOnce() {
    return fetchDownloads().then(onPollSuccess, onPollFailure);
  }

  function setPollRate(intervalMs) {
    if (currentPollMs === intervalMs) return;
    currentPollMs = intervalMs;
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = window.setInterval(pollOnce, intervalMs);
  }

  async function submitDownload(event) {
    event.preventDefault();
    const url = urlInput.value.trim();
    const platform = platformFor(url);
    if (!platform) {
      setNotice(notice, "Not a TikTok or Instagram link.", "error");
      return;
    }
    try {
      const response = await fetch(appPath(`${basePathFor(platform)}?async=1`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!response.ok) throw new Error(await readApiError(response, "Could not start that download."));
      await response.json();
      // Clear on submit so the next link can be pasted immediately.
      urlInput.value = "";
      setNotice(notice, "Entered. Paste another.");
      await fetchDownloads();
    } catch (error) {
      setNotice(notice, error.message, "error");
    }
  }

  resultContainer.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action='delete-download']");
    if (!button) return;
    button.disabled = true;
    try {
      const base = basePathFor(button.dataset.platform);
      const response = await fetch(appPath(`${base}/${button.dataset.id}`), { method: "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "Could not discard it."));
      setNotice(notice, "Discarded.");
      await fetchDownloads();
    } catch (error) {
      button.disabled = false;
      setNotice(notice, error.message, "error");
    }
  });

  form.addEventListener("submit", submitDownload);
  clearButton.addEventListener("click", () => {
    form.reset();
    setNotice(notice, "Cleared.");
  });

  resultContainer.innerHTML = emptyState();
  setPollRate(IDLE_POLL_MS);
  pollOnce();
}
