function initRecordPage() {
  const jobsContainer = document.getElementById("jobs-container");
  const recordForm = document.getElementById("recording-form");
  const recordNotice = document.getElementById("record-notice");
  const recordHelperActions = document.getElementById("record-helper-actions");
  const clearRecordFormButton = document.getElementById("clear-record-form");
  const refreshRecordingsButton = document.getElementById("refresh-recordings");
  const autoRefreshButton = document.getElementById("toggle-auto-refresh");
  let autoRefreshEnabled = true;
  let refreshTimer = null;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function clearRecordHelperActions() { recordHelperActions.innerHTML = ""; }

  function setRecordHelperWatchLink(source, duration) {
    const params = new URLSearchParams();
    if (source) params.set("source", source);
    if (duration) params.set("duration", duration);
    const href = `${appPath("/watch")}${params.toString() ? `?${params.toString()}` : ""}`;
    recordHelperActions.innerHTML = `<a class="btn btn-secondary" href="${href}">Switch to Auto-record →</a>`;
  }

  function formatBytes(value) {
    if (value === null || value === undefined) return "-";
    const units = ["B", "KB", "MB", "GB"];
    let size = value;
    let i = 0;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i += 1; }
    return `${size.toFixed(size >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  }

  function retentionNote(job) {
    if (!job.fetched_at) return "";
    const removesAt = new Date(new Date(job.fetched_at).getTime() + 24 * 3600 * 1000);
    const hoursLeft = Math.max(0, Math.round((removesAt - Date.now()) / 3600000));
    return `Saved — removed in ~${hoursLeft}h`;
  }

  function humanizePhase(progress) {
    return ({ preparing: "Preparing", recording: "Recording", finalizing: "Finalizing", ready: "Ready", failed: "Failed", stopped: "Stopped" })[progress] || progress || "Unknown";
  }

  function badgeClass(value) {
    if (value === "ready") return "good";
    if (value === "failed" || value === "stopped") return "bad";
    return "soft";
  }

  function isLivePhase(value) {
    return value === "preparing" || value === "recording" || value === "finalizing";
  }

  function buildSourcePayload() {
    const source = document.getElementById("record-source").value.trim();
    const duration = document.getElementById("record-duration").value.trim();
    const payload = {};
    if (source) {
      const normalized = source.replace(/^@+/, "").trim();
      const isUrl = /^https?:\/\//i.test(source) || source.includes("tiktok.com/");
      if (isUrl) payload.url = source; else payload.username = normalized;
    }
    if (duration) payload.duration = Number(duration);
    return payload;
  }

  function buildRecordingActions(job) {
    const actions = [];
    if (job.status === "running") actions.push(`<button class="btn btn-warn" data-action="stop" data-id="${job.id}">Stop</button>`);
    if ((job.status === "finished" || job.status === "stopped") && job.file_path) actions.push(`<a class="btn btn-primary" href="${appPath(`/recordings/${job.id}/download`)}">Download</a>`);
    actions.push(`<button class="btn btn-danger" data-action="delete" data-id="${job.id}">Delete</button>`);
    return actions.join("");
  }

  function renderJobs(jobs) {
    if (!jobs.length) {
      jobsContainer.innerHTML = `
        <div class="empty">
          <span class="empty-icon">●</span>
          <span class="empty-title">No recordings yet</span>
          <span>When a recording starts, it will appear here.</span>
        </div>`;
      return;
    }
    jobsContainer.innerHTML = jobs.map((job) => {
      const phase = job.progress;
      const liveClass = isLivePhase(phase) ? " live" : "";
      return `
        <article class="job-card">
          <header class="job-header">
            <div>
              <h3 class="job-title">${escapeHtml(job.username || job.url || "-")}</h3>
              <span class="job-id">${escapeHtml(job.id)}</span>
            </div>
            <span class="status-pill ${badgeClass(phase)}${liveClass}">${escapeHtml(humanizePhase(phase))}</span>
          </header>
          ${job.progress_message ? `<p class="job-message">${escapeHtml(job.progress_message)}</p>` : ""}
          <dl class="job-stats">
            <div><dt>Created</dt><dd>${escapeHtml(formatDate(job.created_at))}</dd></div>
            <div><dt>Started</dt><dd>${escapeHtml(formatDate(job.started_at))}</dd></div>
            <div><dt>Finished</dt><dd>${escapeHtml(formatDate(job.finished_at))}</dd></div>
            <div><dt>Duration</dt><dd>${job.duration ? `${escapeHtml(job.duration)} seconds` : "Until live ends"}</dd></div>
            <div><dt>File size</dt><dd>${escapeHtml(formatBytes(job.file_size_bytes))}</dd></div>
            ${job.fetched_at ? `<div class="wide"><dt>Retention</dt><dd>${escapeHtml(retentionNote(job))}</dd></div>` : ""}
            ${job.file_path ? `<div class="wide"><dt>File</dt><dd>${escapeHtml(job.file_path)}</dd></div>` : ""}
            ${job.error ? `<div class="wide"><dt>Error</dt><dd>${escapeHtml(job.error)}</dd></div>` : ""}
          </dl>
          <footer class="job-actions">${buildRecordingActions(job)}</footer>
        </article>`;
    }).join("");
  }


  // A background poll must not clobber the notice describing what the user just
  // did: a single dropped request used to replace "This user is not live right
  // now." with the browser's raw "Failed to fetch". Tolerate blips, and only
  // speak up once the app is really unreachable.
  const POLL_FAILURES_BEFORE_WARNING = 3;
  let consecutivePollFailures = 0;
  let pollWarningShown = false;

  function onPollSuccess() {
    consecutivePollFailures = 0;
    if (pollWarningShown) {
      pollWarningShown = false;
      setNotice(recordNotice, "Reconnected.");
    }
  }

  function onPollFailure() {
    consecutivePollFailures += 1;
    if (consecutivePollFailures < POLL_FAILURES_BEFORE_WARNING) return;
    pollWarningShown = true;
    setNotice(recordNotice, "Lost connection to the app — still retrying…", "error");
  }

  function pollOnce(load) {
    return load().then(onPollSuccess, onPollFailure);
  }

  async function fetchRecordings() {
    const response = await fetch(appPath("/recordings"));
    if (!response.ok) throw new Error(`Failed to load recordings: ${response.status}`);
    renderJobs(await response.json());
  }

  async function submitRecording(event) {
    event.preventDefault();
    const payload = buildSourcePayload();
    const source = document.getElementById("record-source").value.trim();
    const duration = document.getElementById("record-duration").value.trim();
    clearRecordHelperActions();
    if (!payload.username && !payload.url) {
      setNotice(recordNotice, "Please enter a TikTok username or live URL.", "error");
      return;
    }
    setNotice(recordNotice, "Checking the account and starting the recording...");
    try {
      const response = await fetch(appPath("/recordings"), { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't start the recording."));
      const body = await response.json();
      setNotice(recordNotice, `Recording started. Job ID: ${body.id}`, "success");
      recordForm.reset();
      await fetchRecordings();
    } catch (error) {
      setNotice(recordNotice, error.message, "error");
      if (error.message === "This user is not live right now.") setRecordHelperWatchLink(source, duration);
    }
  }

  async function handleJobAction(event) {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    const jobId = button.dataset.id;
    button.disabled = true;
    try {
      const response = await fetch(action === "stop" ? appPath(`/recordings/${jobId}/stop`) : appPath(`/recordings/${jobId}`), { method: action === "stop" ? "POST" : "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "The action could not be completed."));
      await response.json();
      setNotice(recordNotice, "Recording updated.", "success");
      clearRecordHelperActions();
      await fetchRecordings();
    } catch (error) { setNotice(recordNotice, error.message, "error"); }
    finally { button.disabled = false; }
  }

  function setAutoRefresh(enabled) {
    autoRefreshEnabled = enabled;
    autoRefreshButton.textContent = `Auto-refresh: ${enabled ? "On" : "Off"}`;
    autoRefreshButton.setAttribute("aria-pressed", String(enabled));
    if (refreshTimer) window.clearInterval(refreshTimer);
    refreshTimer = enabled ? window.setInterval(() => pollOnce(fetchRecordings), 5000) : null;
  }

  recordForm.addEventListener("submit", submitRecording);
  clearRecordFormButton.addEventListener("click", () => { recordForm.reset(); clearRecordHelperActions(); setNotice(recordNotice, "Record form cleared."); });
  refreshRecordingsButton.addEventListener("click", () => {
    fetchRecordings().then(onPollSuccess, (error) => setNotice(recordNotice, error.message || "Could not reach the app.", "error"));
  });
  autoRefreshButton.addEventListener("click", () => setAutoRefresh(!autoRefreshEnabled));
  jobsContainer.addEventListener("click", handleJobAction);

  setAutoRefresh(true);
  pollOnce(fetchRecordings);
}
