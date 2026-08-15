function initWatchPage() {
  const watchContainer = document.getElementById("watch-container");
  const watchForm = document.getElementById("watch-form");
  const watchNotice = document.getElementById("watch-notice");
  const clearWatchFormButton = document.getElementById("clear-watch-form");
  const refreshWatchListButton = document.getElementById("refresh-watch-list");

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function badgeClass(value) {
    if (value === "completed") return "good";
    if (value === "failed" || value === "stopped") return "bad";
    return "soft";
  }

  function isLivePhase(value) {
    return value === "watching" || value === "recording";
  }

  function humanizeStatus(value) {
    return ({ watching: "Watching", recording: "Recording", completed: "Completed", failed: "Failed", stopped: "Stopped" })[value] || value || "Unknown";
  }

  function buildSourcePayload() {
    const source = document.getElementById("watch-source").value.trim();
    const duration = document.getElementById("watch-duration").value.trim();
    const payload = {};
    if (source) {
      const normalized = source.replace(/^@+/, "").trim();
      const isUrl = /^https?:\/\//i.test(source) || source.includes("tiktok.com/");
      if (isUrl) payload.url = source; else payload.username = normalized;
    }
    if (duration) payload.duration = Number(duration);
    return payload;
  }

  function buildWatchActions(job) {
    const actions = [];
    if (job.status === "watching" || job.status === "recording") actions.push(`<button class="btn btn-warn" data-action="stop" data-id="${job.id}">Stop watch</button>`);
    if (job.status === "completed" && job.linked_recording_job_id) actions.push(`<a class="btn btn-primary" href="${appPath(`/recordings/${job.linked_recording_job_id}/download`)}">Download recording</a>`);
    actions.push(`<button class="btn btn-danger" data-action="delete" data-id="${job.id}">Delete</button>`);
    return actions.join("");
  }

  function renderWatchJobs(jobs) {
    if (!jobs.length) {
      watchContainer.innerHTML = `
        <div class="empty">
          <span class="empty-icon">○</span>
          <span class="empty-title">Nothing filed yet</span>
          <span>Place an order above.</span>
        </div>`;
      return;
    }
    watchContainer.innerHTML = jobs.map((job) => {
      const liveClass = isLivePhase(job.status) ? " live" : "";
      return `
        <article class="job-card${liveClass}">
          <header class="job-header">
            <div>
              <span class="job-id">No. ${escapeHtml(job.id.slice(0, 4).toUpperCase())} · ${escapeHtml(formatDate(job.created_at))}</span>
              <h3 class="job-title">${escapeHtml(job.username || job.url || "-")}</h3>
            </div>
            <span class="stamp ${badgeClass(job.status)}">${escapeHtml(humanizeStatus(job.status))}</span>
          </header>
          ${job.last_message ? `<p class="job-message">${escapeHtml(job.last_message)}</p>` : ""}
          <dl class="job-stats">
            <div><dt>Last checked</dt><dd>${escapeHtml(formatDate(job.last_checked_at))}</dd></div>
            <div><dt>Created</dt><dd>${escapeHtml(formatDate(job.created_at))}</dd></div>
            <div><dt>Finished</dt><dd>${escapeHtml(formatDate(job.finished_at))}</dd></div>
            <div><dt>Duration</dt><dd>${job.duration ? `${escapeHtml(job.duration)} seconds` : "Until live ends"}</dd></div>
            ${job.linked_recording_job_id ? `<div class="wide"><dt>Linked recording</dt><dd>${escapeHtml(job.linked_recording_job_id)}</dd></div>` : ""}
          </dl>
          <footer class="job-actions">${buildWatchActions(job)}</footer>
        </article>`;
    }).join("");
  }

  async function fetchWatchJobs() {
    const response = await apiFetch("/watch-recordings");
    if (!response.ok) throw new Error(`Failed to load watch jobs: ${response.status}`);
    renderWatchJobs(await response.json());
  }

  function hydrateFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const source = params.get("source");
    const duration = params.get("duration");
    if (source) document.getElementById("watch-source").value = source;
    if (duration) document.getElementById("watch-duration").value = duration;
    if (source) setNotice(watchNotice, "The creator is offline right now. You can start an auto-record here instead.");
  }

  async function submitWatch(event) {
    event.preventDefault();
    const payload = buildSourcePayload();
    if (!payload.username && !payload.url) {
      setNotice(watchNotice, "Please enter a TikTok username or live URL.", "error");
      return;
    }
    setNotice(watchNotice, "Creating a watch and checking the account...");
    try {
      const response = await apiFetch("/watch-recordings", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't create the watch."));
      await response.json();
      setNotice(watchNotice, "Auto-record is active. Recording will start automatically when the live becomes available.", "success");
      watchForm.reset();
      await fetchWatchJobs();
    } catch (error) { setNotice(watchNotice, error.message, "error"); }
  }

  async function handleWatchAction(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    const watchId = button.dataset.id;
    button.disabled = true;
    try {
      const response = await fetch(action === "stop" ? appPath(`/watch-recordings/${watchId}/stop`) : appPath(`/watch-recordings/${watchId}`), { method: action === "stop" ? "POST" : "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "The action could not be completed."));
      await response.json();
      setNotice(watchNotice, "Watch list updated.", "success");
      await fetchWatchJobs();
    } catch (error) { setNotice(watchNotice, error.message, "error"); }
    finally { button.disabled = false; }
  }

  watchForm.addEventListener("submit", submitWatch);
  clearWatchFormButton.addEventListener("click", () => { watchForm.reset(); setNotice(watchNotice, "Watch form cleared."); });
  refreshWatchListButton.addEventListener("click", () => { fetchWatchJobs().catch((error) => setNotice(watchNotice, error.message, "error")); });
  watchContainer.addEventListener("click", handleWatchAction);

  hydrateFromQuery();
  fetchWatchJobs().catch((error) => setNotice(watchNotice, error.message, "error"));
}
