/**
 * Playwright Firefox wired up the way the user's own Firefox is.
 *
 * The PAC file at ~/.proxy.pac sends the social domains through the SSH SOCKS
 * proxy and everything else direct. Playwright has no PAC support, so the same
 * split is expressed as a SOCKS proxy plus a bypass list: TikTok/Instagram
 * tunnel out through the VPS, the app itself is reached directly.
 */

import { firefox } from "@playwright/test";

export const SOCKS_PROXY = process.env.ROBOT_SOCKS ?? "socks5://127.0.0.1:1080";

export async function launchFirefox({ cookies = [], bypassHosts = [], headless = true } = {}) {
  const browser = await firefox.launch({
    headless,
    proxy: {
      server: SOCKS_PROXY,
      bypass: ["127.0.0.1", "localhost", ...bypassHosts].join(","),
    },
  });

  const context = await browser.newContext({
    viewport: { width: 1280, height: 900 },
    locale: "en-US",
  });
  if (cookies.length) await context.addCookies(cookies);

  return {
    browser,
    context,
    async close() {
      await context.close().catch(() => {});
      await browser.close().catch(() => {});
    },
  };
}

const CHALLENGE_PATTERN = /Drag the slider|fit the puzzle|Verify to continue|unusual traffic/i;

/**
 * TikTok sometimes serves a slider CAPTCHA. The robot never solves it — that is
 * a line it does not cross, and defeating it would only escalate an arms race
 * against the account the production server depends on. In a headed run the
 * person at the keyboard can clear it, so pause and let them; headless just
 * reports and moves on.
 */
export async function waitOutChallenge(page, { headed = false, log = () => {}, timeoutMs = 180000 } = {}) {
  const present = async () => {
    try {
      return CHALLENGE_PATTERN.test(await page.locator("body").innerText());
    } catch {
      return false;
    }
  };

  if (!(await present())) return false;

  if (!headed) {
    log("  ⚠ TikTok is showing a CAPTCHA. Re-run with --headed to solve it yourself.");
    return true;
  }

  log("");
  log("  ⚠ TikTok is showing its slider CAPTCHA in the browser window.");
  log("    Solve it there and the run will continue on its own. I will not solve it.");
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await page.waitForTimeout(2000);
    if (!(await present())) {
      log("  ✓ challenge cleared, carrying on");
      return false;
    }
  }
  log("  ⚠ challenge still up after waiting; continuing without it");
  return true;
}
