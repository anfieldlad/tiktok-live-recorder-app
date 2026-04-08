function initWatchPage() {
  const { refreshSessionStatus, sessionNotice } = initSessionPanel();
  const watchContainer = document.getElementById("watch-container");
  const watchForm = document.getElementById("watch-form");
  const watchNotice = document.getElementById("watch-notice");
  const clearWatchFormButton = document.getElementById("clear-watch-form");
  const refreshWatchListButton = document.getElementById("refresh-watch-list");

  function badgeClass(value) { if (value === "completed") return "good"; if (value === "failed" || value === "stopped") return "bad"; return "soft"; }
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
    if (job.status === "watching" || job.status === "recording") actions.push(`<button class="warn" data-action="stop" data-id="${job.id}">Stop watch</button>`);
    actions.push(`<button class="danger" data-action="delete" data-id="${job.id}">Delete</button>`);
    return actions.join("");
  }
  function renderWatchJobs(jobs) {
    if (!jobs.length) { watchContainer.innerHTML = '<div class="empty">No watches yet. Create one when you want the app to wait for a live and start automatically.</div>'; return; }
    watchContainer.innerHTML = jobs.map((job) => `
      <article class="card">
        <div class="job-top">
          <div class="job-identity">
            <h3 class="job-name">${job.username || job.url || "-"}</h3>
            <div class="job-id">${job.id}</div>
          </div>
          <span class="pill status-pill ${badgeClass(job.status)}">${job.status}</span>
        </div>
        <div class="job-meta">
          <div class="meta-card wide"><p class="meta-label">Latest update</p><p class="meta-value">${job.last_message || "-"}</p></div>
          <div class="meta-card"><p class="meta-label">Last checked</p><p class="meta-value">${formatDate(job.last_checked_at)}</p></div>
          <div class="meta-card"><p class="meta-label">Created</p><p class="meta-value">${formatDate(job.created_at)}</p></div>
          <div class="meta-card"><p class="meta-label">Finished</p><p class="meta-value">${formatDate(job.finished_at)}</p></div>
          <div class="meta-card"><p class="meta-label">Duration</p><p class="meta-value">${job.duration ? `${job.duration} seconds` : "Until the live ends"}</p></div>
          <div class="meta-card wide"><p class="meta-label">Linked recording</p><p class="meta-value">${job.linked_recording_job_id || "-"}</p></div>
        </div>
        <div class="job-actions">${buildWatchActions(job)}</div>
      </article>`).join("");
  }
  async function fetchWatchJobs() {
    const response = await fetch(appPath("/watch-recordings"));
    if (!response.ok) throw new Error(`Failed to load watch jobs: ${response.status}`);
    renderWatchJobs(await response.json());
  }
  function hydrateFromQuery() {
    const params = new URLSearchParams(window.location.search);
    const source = params.get("source");
    const duration = params.get("duration");
    if (source) document.getElementById("watch-source").value = source;
    if (duration) document.getElementById("watch-duration").value = duration;
    if (source) setNotice(watchNotice, "The creator is offline right now. You can start watch mode here instead.");
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
      const response = await fetch(appPath("/watch-recordings"), { method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify(payload) });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't create the watch."));
      await response.json();
      setNotice(watchNotice, "Watch mode is active. Recording will start automatically when the live becomes available.", "success");
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
  refreshSessionStatus().catch((error) => setNotice(sessionNotice, error.message, "error"));
  fetchWatchJobs().catch((error) => setNotice(watchNotice, error.message, "error"));
}
