function initRecordPage() {
  const { refreshSessionStatus, sessionNotice } = initSessionPanel();
  const jobsContainer = document.getElementById("jobs-container");
  const recordForm = document.getElementById("recording-form");
  const recordNotice = document.getElementById("record-notice");
  const recordHelperActions = document.getElementById("record-helper-actions");
  const clearRecordFormButton = document.getElementById("clear-record-form");
  const refreshRecordingsButton = document.getElementById("refresh-recordings");
  const autoRefreshButton = document.getElementById("toggle-auto-refresh");
  let autoRefreshEnabled = true;
  let refreshTimer = null;

  function clearRecordHelperActions() { recordHelperActions.innerHTML = ""; }
  function setRecordHelperWatchLink(source, duration) {
    const params = new URLSearchParams();
    if (source) params.set("source", source);
    if (duration) params.set("duration", duration);
    const href = `${appPath("/watch")}${params.toString() ? `?${params.toString()}` : ""}`;
    recordHelperActions.innerHTML = `<a class="button-link secondary" href="${href}">Open Watch Mode</a>`;
  }
  function formatBytes(value) {
    if (value === null || value === undefined) return "-";
    const units = ["B", "KB", "MB", "GB"];
    let size = value;
    let i = 0;
    while (size >= 1024 && i < units.length - 1) { size /= 1024; i += 1; }
    return `${size.toFixed(size >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
  }
  function humanizePhase(progress) { return ({ preparing:"Preparing", recording:"Recording", finalizing:"Finalizing", ready:"Ready", failed:"Failed", stopped:"Stopped" })[progress] || progress || "Unknown"; }
  function badgeClass(value) { if (value === "ready") return "good"; if (value === "failed" || value === "stopped") return "bad"; return "soft"; }
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
    if (job.status === "running") actions.push(`<button class="warn" data-action="stop" data-id="${job.id}">Stop</button>`);
    if ((job.status === "finished" || job.status === "stopped") && job.file_path) actions.push(`<a class="button-link primary" href="${appPath(`/recordings/${job.id}/download`)}">Download</a>`);
    actions.push(`<button class="danger" data-action="delete" data-id="${job.id}">Delete</button>`);
    return actions.join("");
  }
  function renderJobs(jobs) {
    if (!jobs.length) { jobsContainer.innerHTML = '<div class="empty">No recordings yet. When a recording starts, it will appear here.</div>'; return; }
    jobsContainer.innerHTML = jobs.map((job) => `
      <article class="card">
        <div class="job-top">
          <div class="job-identity">
            <h3 class="job-name">${job.username || job.url || "-"}</h3>
            <div class="job-id">${job.id}</div>
          </div>
          <span class="pill status-pill ${badgeClass(job.progress)}">${humanizePhase(job.progress)}</span>
        </div>
        <div class="job-meta">
          <div class="meta-card wide"><p class="meta-label">Status message</p><p class="meta-value">${job.progress_message || "-"}</p></div>
          <div class="meta-card"><p class="meta-label">Created</p><p class="meta-value">${formatDate(job.created_at)}</p></div>
          <div class="meta-card"><p class="meta-label">Started</p><p class="meta-value">${formatDate(job.started_at)}</p></div>
          <div class="meta-card"><p class="meta-label">Finished</p><p class="meta-value">${formatDate(job.finished_at)}</p></div>
          <div class="meta-card"><p class="meta-label">Duration</p><p class="meta-value">${job.duration ? `${job.duration} seconds` : "Until the live ends"}</p></div>
          <div class="meta-card"><p class="meta-label">File size</p><p class="meta-value">${formatBytes(job.file_size_bytes)}</p></div>
          <div class="meta-card wide"><p class="meta-label">File</p><p class="meta-value">${job.file_path || "-"}</p></div>
          <div class="meta-card wide"><p class="meta-label">Error</p><p class="meta-value">${job.error || "-"}</p></div>
        </div>
        <div class="job-actions">${buildRecordingActions(job)}</div>
      </article>`).join("");
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
      const response = await fetch(appPath("/recordings"), { method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify(payload) });
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
    autoRefreshButton.textContent = `Auto Refresh: ${enabled ? "On" : "Off"}`;
    if (refreshTimer) window.clearInterval(refreshTimer);
    refreshTimer = enabled ? window.setInterval(() => { fetchRecordings().catch((error) => setNotice(recordNotice, error.message, "error")); }, 5000) : null;
  }

  recordForm.addEventListener("submit", submitRecording);
  clearRecordFormButton.addEventListener("click", () => { recordForm.reset(); clearRecordHelperActions(); setNotice(recordNotice, "Record form cleared."); });
  refreshRecordingsButton.addEventListener("click", () => { fetchRecordings().catch((error) => setNotice(recordNotice, error.message, "error")); });
  autoRefreshButton.addEventListener("click", () => setAutoRefresh(!autoRefreshEnabled));
  jobsContainer.addEventListener("click", handleJobAction);

  setAutoRefresh(true);
  refreshSessionStatus().catch((error) => setNotice(sessionNotice, error.message, "error"));
  fetchRecordings().catch((error) => setNotice(recordNotice, error.message, "error"));
}
