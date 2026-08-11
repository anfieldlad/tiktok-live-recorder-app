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
