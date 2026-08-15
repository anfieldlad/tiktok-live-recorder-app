/**
 * The masthead carries one dot for two platforms, so neither panel owns it
 * alone: each reports its own state here and the dot only reads ready when
 * both sessions are saved *and* this browser may use them. Without the second
 * half, a visitor with no key would be told the session is ready when it is
 * not theirs to spend.
 */
window.sessionStates = window.sessionStates || {};
window.reportSessionState = function reportSessionState(platform, configured, allowed = true) {
  window.sessionStates[platform] = Boolean(configured) && Boolean(allowed);
  const dot = document.getElementById("session-dot");
  if (!dot) return;
  const known = Object.values(window.sessionStates);
  const allReady = known.length === 2 && known.every(Boolean);
  dot.classList.toggle("is-off", !allReady);
};

function initSessionPanel() {
  const sessionNotice = document.getElementById("session-notice");
  const cookiesForm = document.getElementById("cookies-form");
  const clearCookiesButton = document.getElementById("clear-cookies");
  const importChromeButton = document.getElementById("import-chrome");
  const importEdgeButton = document.getElementById("import-edge");
  const loginChromeButton = document.getElementById("login-chrome");
  const loginEdgeButton = document.getElementById("login-edge");
  const captureLoginButton = document.getElementById("capture-login");
  const closeLoginButton = document.getElementById("close-login");
  const sessionToggle = document.getElementById("session-toggle");
  const sessionDrawer = document.getElementById("session-drawer");
  const sessionCloseButton = document.getElementById("session-close");

  function setBrowserLoginControlsEnabled(enabled) {
    if (loginChromeButton) loginChromeButton.disabled = !enabled;
    if (loginEdgeButton) loginEdgeButton.disabled = !enabled;
    if (captureLoginButton) captureLoginButton.disabled = !enabled;
    if (closeLoginButton) closeLoginButton.disabled = !enabled;
  }

  function setSessionState(configured, allowed) {
    window.reportSessionState("tiktok", configured, allowed);
  }

  function openDrawer() {
    if (!sessionDrawer || !sessionToggle) return;
    sessionDrawer.classList.add("is-open");
    sessionDrawer.setAttribute("aria-hidden", "false");
    sessionToggle.setAttribute("aria-expanded", "true");
  }

  function closeDrawer() {
    if (!sessionDrawer || !sessionToggle) return;
    sessionDrawer.classList.remove("is-open");
    sessionDrawer.setAttribute("aria-hidden", "true");
    sessionToggle.setAttribute("aria-expanded", "false");
  }

  function toggleDrawer() {
    if (!sessionDrawer) return;
    if (sessionDrawer.classList.contains("is-open")) closeDrawer();
    else openDrawer();
  }

  async function refreshSessionStatus() {
    const [cookieResponse, loginResponse] = await Promise.all([apiFetch("/auth/status"), apiFetch("/auth/login-browser/status")]);
    if (!cookieResponse.ok || !loginResponse.ok) throw new Error("Couldn't read the TikTok session.");
    const cookieBody = await cookieResponse.json();
    const loginBody = await loginResponse.json();
    setBrowserLoginControlsEnabled(loginBody.browser_launch_supported);
    setSessionState(Boolean(cookieBody.configured), Boolean(cookieBody.session_allowed));
    if (!sessionNotice) return;
    if (!cookieBody.session_allowed) setNotice(sessionNotice, "Add the server key above to use or change this session.");
    else if (cookieBody.configured) setNotice(sessionNotice, "Your TikTok session is ready.", "success");
    else if (!loginBody.browser_launch_supported) setNotice(sessionNotice, "Guided login is Windows-only. On this server, paste session_ss below.");
    else if (loginBody.browser_open) setNotice(sessionNotice, "Login window open. Sign in, close it, then Capture session.");
    else setNotice(sessionNotice, "No session saved. You only need one for private or age-restricted lives.");
  }

  async function saveCookies(event) {
    event.preventDefault();
    const sessionInput = document.getElementById("session-ss");
    const sessionValue = sessionInput.value.trim();
    if (!sessionValue) { setNotice(sessionNotice, "Enter a session_ss value.", "error"); return; }
    try {
      const response = await apiFetch("/auth/tiktok-cookies", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session_ss: sessionValue }) });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't save the TikTok session."));
      await response.json();
      sessionInput.value = "";
      await refreshSessionStatus();
      setNotice(sessionNotice, "TikTok session saved.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function clearCookies() {
    try {
      const response = await apiFetch("/auth/tiktok-cookies", { method: "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't clear the TikTok session."));
      await response.json();
      const sessionInput = document.getElementById("session-ss");
      if (sessionInput) sessionInput.value = "";
      await refreshSessionStatus();
      setNotice(sessionNotice, "TikTok session cleared.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function importCookies(browserName) {
    try {
      setNotice(sessionNotice, `Importing from ${browserName}…`);
      const response = await apiFetch(`/auth/import-browser/${browserName}`, { method: "POST" });
      if (!response.ok) throw new Error(await readApiError(response, `Couldn't import cookies from ${browserName}.`));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, `TikTok session imported from ${browserName}.`, "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function startBrowserLogin(browserName) {
    try {
      setNotice(sessionNotice, `Opening a login window in ${browserName}…`);
      const response = await apiFetch(`/auth/login-browser/${browserName}/start`, { method: "POST" });
      if (!response.ok) throw new Error(await readApiError(response, `Couldn't open the login browser in ${browserName}.`));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, `Login window open in ${browserName}. Sign in, close it, then Capture session.`, "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function captureBrowserLogin() {
    try {
      setNotice(sessionNotice, "Capturing the TikTok session…");
      const response = await apiFetch("/auth/login-browser/capture", { method: "POST" });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't capture the TikTok session."));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, "TikTok session captured and saved.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function closeBrowserLogin() {
    try {
      const response = await apiFetch("/auth/login-browser/close", { method: "POST" });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't close the login browser."));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, "Login browser closed.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  const apiKeyForm = document.getElementById("api-key-form");
  const apiKeyInput = document.getElementById("api-key");
  const forgetApiKeyButton = document.getElementById("forget-api-key");
  const apiKeyNotice = document.getElementById("api-key-notice");

  function showApiKeyState() {
    if (!apiKeyNotice) return;
    const saved = Boolean(getApiKey());
    setNotice(apiKeyNotice, saved ? "Key saved in this browser." : "Kept in this browser only.", saved ? "success" : "");
  }

  // Both panels have to re-read their status: the key changes what the server
  // is willing to tell each of them.
  async function refreshBothPanels() {
    await refreshSessionStatus().catch(() => {});
    if (window.refreshIgSessionStatus) await window.refreshIgSessionStatus().catch(() => {});
  }

  if (apiKeyForm) {
    apiKeyForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      setApiKey(apiKeyInput.value.trim());
      apiKeyInput.value = "";
      showApiKeyState();
      await refreshBothPanels();
    });
  }
  if (forgetApiKeyButton) {
    forgetApiKeyButton.addEventListener("click", async () => {
      setApiKey("");
      if (apiKeyInput) apiKeyInput.value = "";
      showApiKeyState();
      await refreshBothPanels();
    });
  }
  showApiKeyState();

  if (cookiesForm) cookiesForm.addEventListener("submit", saveCookies);
  if (clearCookiesButton) clearCookiesButton.addEventListener("click", clearCookies);
  if (importChromeButton) importChromeButton.addEventListener("click", () => importCookies("chrome"));
  if (importEdgeButton) importEdgeButton.addEventListener("click", () => importCookies("edge"));
  if (loginChromeButton) loginChromeButton.addEventListener("click", () => startBrowserLogin("chrome"));
  if (loginEdgeButton) loginEdgeButton.addEventListener("click", () => startBrowserLogin("edge"));
  if (captureLoginButton) captureLoginButton.addEventListener("click", captureBrowserLogin);
  if (closeLoginButton) closeLoginButton.addEventListener("click", closeBrowserLogin);
  if (sessionToggle) sessionToggle.addEventListener("click", toggleDrawer);
  if (sessionCloseButton) sessionCloseButton.addEventListener("click", closeDrawer);

  refreshSessionStatus().catch((error) => {
    if (sessionNotice) setNotice(sessionNotice, error.message, "error");
  });

  return { refreshSessionStatus, sessionNotice, openDrawer, closeDrawer };
}
