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

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";

const FEEDS = ["https://www.tiktok.com/live", "https://www.tiktok.com/live/following"];

const SEEN_FILE = ".robot-out/seen-live.json";
const SEEN_LIMIT = 50;

/**
 * Names seen live on earlier runs.
 *
 * TikTok throttles the LIVE feeds after a few automated visits, and once that
 * happens every feed returns "No LIVE streams for you yet" on a perfectly
 * signed-in page. Creators who were live yesterday are usually live again, so
 * the cache turns a throttled run into a working one.
 */
export function recallSeenLive() {
  try {
    if (!existsSync(SEEN_FILE)) return [];
    const parsed = JSON.parse(readFileSync(SEEN_FILE, "utf8"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function rememberSeenLive(names) {
  if (!names.length) return;
  const merged = [...new Set([...names, ...recallSeenLive()])].slice(0, SEEN_LIMIT);
  try {
    mkdirSync(".robot-out", { recursive: true });
    writeFileSync(SEEN_FILE, `${JSON.stringify(merged, null, 2)}\n`);
  } catch {
    // the cache is an optimisation; losing it is not worth failing a run
  }
}

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
        rememberSeenLive(found);
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
 * Accounts the app already knows about, from watch jobs and recording history.
 *
 * TikTok soft-throttles the LIVE feeds after a handful of automated visits —
 * every feed, including the curated category tabs, starts answering "No LIVE
 * streams for you yet" while still serving a signed-in page. When that happens
 * the feed is worthless but these names are not: they are creators the user
 * actually follows, so one of them is often live.
 */
export async function knownAccounts(client, { log = () => {} } = {}) {
  const names = new Set();
  const harvest = (jobs) => {
    for (const job of jobs ?? []) {
      const name = job.username || (job.url ?? "").match(/\/@([A-Za-z0-9._]+)/)?.[1];
      if (name) names.add(name);
    }
  };

  try {
    harvest(await client.listWatchRecordings());
  } catch (error) {
    log(`  watch list unavailable: ${error.message}`);
  }
  try {
    harvest(await client.listRecordings());
  } catch (error) {
    log(`  recording list unavailable: ${error.message}`);
  }

  const found = [...names];
  if (found.length) log(`  ${found.length} account(s) the app already knows`);
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
