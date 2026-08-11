/**
 * Find TikTok accounts that are live right now.
 *
 * The LIVE feed is only populated for a signed-in session — logged out it says
 * "No LIVE streams for you yet" — so this runs in a context carrying the
 * borrowed Firefox cookies. Scraping is deliberately loose: the app's own
 * check-live endpoint is the oracle that decides whether a candidate is really
 * recordable, so a stale or bogus name here costs one extra API call, nothing
 * more.
 */

const FEEDS = ["https://www.tiktok.com/live", "https://www.tiktok.com/live/following"];

// Nav and product links that live under /@ or look like usernames but are not.
const NOT_USERNAMES = new Set(["live", "following", "foryou", "explore", "upload", "search"]);

export async function discoverLiveUsernames(context, { limit = 12, log = () => {} } = {}) {
  const page = await context.newPage();
  const found = [];

  try {
    for (const feed of FEEDS) {
      if (found.length >= limit) break;
      try {
        const response = await page.goto(feed, { waitUntil: "domcontentloaded", timeout: 60000 });
        if (!response?.ok()) {
          log(`  feed ${feed} returned ${response?.status()}`);
          continue;
        }
        // The feed hydrates client-side; give the cards a moment to render.
        await page.waitForTimeout(6000);

        const text = await page.locator("body").innerText();
        if (/Log in to|You need to log in/i.test(text)) {
          log("  feed wants a login — the borrowed session may have expired");
          continue;
        }

        const hrefs = await page.$$eval("a[href*='/@']", (anchors) =>
          anchors.map((anchor) => anchor.getAttribute("href")).filter(Boolean),
        );
        // Prefer explicit /@name/live links, then bare profile links.
        const ordered = [
          ...hrefs.filter((href) => /\/@[^/]+\/live/.test(href)),
          ...hrefs.filter((href) => !/\/@[^/]+\/live/.test(href)),
        ];
        for (const href of ordered) {
          const name = (href.match(/\/@([A-Za-z0-9._]+)/) || [])[1];
          if (!name || NOT_USERNAMES.has(name.toLowerCase())) continue;
          if (!found.includes(name)) found.push(name);
          if (found.length >= limit) break;
        }
        log(`  ${feed} -> ${found.length} candidate(s)`);
      } catch (error) {
        log(`  feed ${feed} failed: ${error.message}`);
      }
    }
  } finally {
    await page.close().catch(() => {});
  }

  return found;
}

/**
 * Ask the app which candidate is actually recordable. The app is the oracle
 * because it is the thing under test: if it disagrees with TikTok's own feed,
 * that is a finding, not a bug in the robot.
 */
export async function firstRecordable(client, usernames, { log = () => {} } = {}) {
  for (const username of usernames) {
    try {
      const status = await client.checkLive({ username });
      log(`  @${username}: ${status.can_record ? "RECORDABLE" : status.message}`);
      if (status.can_record) return { username, status };
    } catch (error) {
      log(`  @${username}: check failed (${error.message})`);
    }
  }
  return null;
}
