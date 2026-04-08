function initSessionPanel() {
  const sessionNotice = document.getElementById("session-notice");
  const cookiesForm = document.getElementById("cookies-form");
  const clearCookiesButton = document.getElementById("clear-cookies");
  const importChromeButton = document.getElementById("import-chrome");
  const importEdgeButton = document.getElementById("import-edge");
  const sessionPill = document.getElementById("session-pill");
  const loginChromeButton = document.getElementById("login-chrome");
  const loginEdgeButton = document.getElementById("login-edge");
  const captureLoginButton = document.getElementById("capture-login");
  const closeLoginButton = document.getElementById("close-login");

  function setBrowserLoginControlsEnabled(enabled) {
    loginChromeButton.disabled = !enabled;
    loginEdgeButton.disabled = !enabled;
    captureLoginButton.disabled = !enabled;
    closeLoginButton.disabled = !enabled;
  }

  async function refreshSessionStatus() {
    const [cookieResponse, loginResponse] = await Promise.all([fetch(appPath("/auth/status")), fetch(appPath("/auth/login-browser/status"))]);
    if (!cookieResponse.ok || !loginResponse.ok) throw new Error("Failed to load TikTok session status");
    const cookieBody = await cookieResponse.json();
    const loginBody = await loginResponse.json();
    setBrowserLoginControlsEnabled(loginBody.browser_launch_supported);
    sessionPill.textContent = cookieBody.configured ? "Session ready" : "Session needed";
    sessionPill.className = `pill ${cookieBody.configured ? "good" : "soft"}`;
    if (cookieBody.configured) setNotice(sessionNotice, "Your TikTok session is ready.", "success");
    else if (!loginBody.browser_launch_supported) setNotice(sessionNotice, "Guided Chrome or Edge login is available on Windows only. On this server, save session_ss manually or import cookies another way.");
    else if (loginBody.browser_open) setNotice(sessionNotice, "The TikTok login window is open. Finish signing in there, then close it and click Capture session.");
    else setNotice(sessionNotice, "No TikTok session is saved yet. You only need this for private or restricted lives.");
  }

  async function saveCookies(event) {
    event.preventDefault();
    const sessionValue = document.getElementById("session-ss").value.trim();
    if (!sessionValue) { setNotice(sessionNotice, "Please enter a session_ss value.", "error"); return; }
    try {
      const response = await fetch(appPath("/auth/tiktok-cookies"), { method:"POST", headers:{ "Content-Type":"application/json" }, body:JSON.stringify({ session_ss: sessionValue }) });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't save the TikTok session."));
      await response.json();
      document.getElementById("session-ss").value = "";
      await refreshSessionStatus();
      setNotice(sessionNotice, "TikTok session saved.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function clearCookies() {
    try {
      const response = await fetch(appPath("/auth/tiktok-cookies"), { method:"DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't clear the TikTok session."));
      await response.json();
      document.getElementById("session-ss").value = "";
      await refreshSessionStatus();
      setNotice(sessionNotice, "TikTok session cleared.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function importCookies(browserName) {
    try {
      setNotice(sessionNotice, `Importing TikTok session from ${browserName}...`);
      const response = await fetch(appPath(`/auth/import-browser/${browserName}`), { method:"POST" });
      if (!response.ok) throw new Error(await readApiError(response, `Couldn't import cookies from ${browserName}.`));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, `TikTok session imported from ${browserName}.`, "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function startBrowserLogin(browserName) {
    try {
      setNotice(sessionNotice, `Opening a TikTok login window in ${browserName}...`);
      const response = await fetch(appPath(`/auth/login-browser/${browserName}/start`), { method:"POST" });
      if (!response.ok) throw new Error(await readApiError(response, `Couldn't open the login browser in ${browserName}.`));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, `The TikTok login window is open in ${browserName}. Finish signing in there, then close that window and click Capture session.`, "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function captureBrowserLogin() {
    try {
      setNotice(sessionNotice, "Capturing the TikTok session...");
      const response = await fetch(appPath("/auth/login-browser/capture"), { method:"POST" });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't capture the TikTok session."));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, "TikTok session captured and saved.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  async function closeBrowserLogin() {
    try {
      const response = await fetch(appPath("/auth/login-browser/close"), { method:"POST" });
      if (!response.ok) throw new Error(await readApiError(response, "Couldn't close the login browser."));
      await response.json();
      await refreshSessionStatus();
      setNotice(sessionNotice, "Login browser closed.", "success");
    } catch (error) { setNotice(sessionNotice, error.message, "error"); }
  }

  cookiesForm.addEventListener("submit", saveCookies);
  clearCookiesButton.addEventListener("click", clearCookies);
  importChromeButton.addEventListener("click", () => importCookies("chrome"));
  importEdgeButton.addEventListener("click", () => importCookies("edge"));
  loginChromeButton.addEventListener("click", () => startBrowserLogin("chrome"));
  loginEdgeButton.addEventListener("click", () => startBrowserLogin("edge"));
  captureLoginButton.addEventListener("click", captureBrowserLogin);
  closeLoginButton.addEventListener("click", closeBrowserLogin);

  return { refreshSessionStatus, sessionNotice };
}
