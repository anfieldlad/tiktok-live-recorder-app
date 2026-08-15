function initIgSessionPanel() {
  const sessionNotice = document.getElementById("ig-session-notice");
  const cookiesForm = document.getElementById("ig-cookies-form");
  const clearCookiesButton = document.getElementById("ig-clear-cookies");
  const importChromeButton = document.getElementById("ig-import-chrome");
  const importEdgeButton = document.getElementById("ig-import-edge");
  const loginChromeButton = document.getElementById("ig-login-chrome");
  const loginEdgeButton = document.getElementById("ig-login-edge");
  const captureLoginButton = document.getElementById("ig-capture-login");
  const closeLoginButton = document.getElementById("ig-close-login");

  function setBrowserLoginControlsEnabled(enabled) {
    if (loginChromeButton) loginChromeButton.disabled = !enabled;
    if (loginEdgeButton) loginEdgeButton.disabled = !enabled;
    if (captureLoginButton) captureLoginButton.disabled = !enabled;
    if (closeLoginButton) closeLoginButton.disabled = !enabled;
  }

  // The drawer itself belongs to session-panel.js. Two scripts binding the
  // same toggle would open and close it on one click.
  function setSessionState(configured, allowed) {
    window.reportSessionState("instagram", configured, allowed);
  }

  async function refreshSessionStatus() {
    const [cookieResponse, loginResponse] = await Promise.all([apiFetch("/instagram/auth/status"), apiFetch("/instagram/auth/login-browser/status")]);
    if (!cookieResponse.ok || !loginResponse.ok) throw new Error("Couldn't read the Instagram session.");
    const cookieBody = await cookieResponse.json();
    const loginBody = await loginResponse.json();
    setBrowserLoginControlsEnabled(loginBody.browser_launch_supported);
    setSessionState(Boolean(cookieBody.configured), Boolean(cookieBody.session_allowed));
    if (!sessionNotice) return;
    if (!cookieBody.session_allowed) setNotice(sessionNotice, "Add the server key above to use or change this session.");
    else if (cookieBody.configured) setNotice(sessionNotice, "Your Instagram session is ready.", "success");
    else if (!loginBody.browser_launch_supported) setNotice(sessionNotice, "Guided login is Windows-only. On this server, paste sessionid below.");
    else if (loginBody.browser_open) setNotice(sessionNotice, "Login window open. Sign in, close it, then Capture session.");
    else setNotice(sessionNotice, "No session saved. You need one for stories, highlights, and most posts.");
  }

  async function saveCookies(event) {
    event.preventDefault();
    const sessionInput = document.getElementById("ig-session-id");
    const sessionValue = sessionInput.value.trim();
    if (!sessionValue) { setNotice(sessionNotice, "Enter a sessionid value.", "error"); return; }
    try {
      const response = await apiFetch("/instagram/auth/cookies", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sessionid: sessionValue }) });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't save the Instagram session."));
      await response.json();
      sessionInput.value = "";
      await refreshSessionStatus();
      setNotice(sessionNotice, "Instagram session saved.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function clearCookies() {
    try {
      const response = await apiFetch("/instagram/auth/cookies", { method: "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't clear the Instagram session."));
      await response.json();
      const sessionInput = document.getElementById("ig-session-id");
      if (sessionInput) sessionInput.value = "";
      await refreshSessionStatus();
      setNotice(sessionNotice, "Instagram session cleared.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function importCookies(browserName) {
    try {
      setNotice(sessionNotice, `Importing from ${browserName}…`);
      const response = await apiFetch(`/instagram/auth/import-browser/${browserName}`, { method: "POST" });
      if (!response.ok) throw new Error(await readApiError(response, `Couldn't import cookies from ${browserName}.`));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, `Instagram session imported from ${browserName}.`, "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function startBrowserLogin(browserName) {
    try {
      setNotice(sessionNotice, `Opening a login window in ${browserName}…`);
      const response = await apiFetch(`/instagram/auth/login-browser/${browserName}/start`, { method: "POST" });
      if (!response.ok) throw new Error(await readApiError(response, `Couldn't open the login browser in ${browserName}.`));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, `The Instagram login window is open in ${browserName}. Finish signing in, then close that window and click Capture session.`, "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function captureBrowserLogin() {
    try {
      setNotice(sessionNotice, "Capturing the Instagram session…");
      const response = await apiFetch("/instagram/auth/login-browser/capture", { method: "POST" });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't capture the Instagram session."));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, "Instagram session captured and saved.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function closeBrowserLogin() {
    try {
      const response = await apiFetch("/instagram/auth/login-browser/close", { method: "POST" });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't close the login browser."));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, "Login browser closed.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  if (cookiesForm) cookiesForm.addEventListener("submit", saveCookies);
  if (clearCookiesButton) clearCookiesButton.addEventListener("click", clearCookies);
  if (importChromeButton) importChromeButton.addEventListener("click", () => importCookies("chrome"));
  if (importEdgeButton) importEdgeButton.addEventListener("click", () => importCookies("edge"));
  if (loginChromeButton) loginChromeButton.addEventListener("click", () => startBrowserLogin("chrome"));
  if (loginEdgeButton) loginEdgeButton.addEventListener("click", () => startBrowserLogin("edge"));
  if (captureLoginButton) captureLoginButton.addEventListener("click", captureBrowserLogin);
  if (closeLoginButton) closeLoginButton.addEventListener("click", closeBrowserLogin);

  refreshSessionStatus().catch((error) => {
    if (sessionNotice) setNotice(sessionNotice, error.message, "error");
  });

  // The key form in the drawer needs to re-run this after a key changes.
  window.refreshIgSessionStatus = refreshSessionStatus;

  return { refreshSessionStatus, sessionNotice };
}
