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

import { waitOutChallenge } from "./browser.mjs";

// TikTok's LIVE recommendation feed frequently renders "No LIVE streams for you
// yet" for this client even while creators are demonstrably live — the page is
// signed in (its passport endpoint returns 200) and fully rendered, the feed is
// simply withheld. Live *search* is a different backend and keeps working, so
// the feeds are tried first but never relied on.
const FEEDS = ["https://www.tiktok.com/live", "https://www.tiktok.com/live/following"];

const SEARCH_TERMS = (process.env.ROBOT_SEARCH_TERMS ?? "indonesia,live,gaming")
  .split(",")
  .map((term) => term.trim())
  .filter(Boolean);

const searchUrl = (term) => `https://www.tiktok.com/search/live?q=${encodeURIComponent(term)}`;

const SEEN_FILE = ".robot-out/seen-live.json";
const SEEN_LIMIT = 50;
const FOLLOWS_CACHE = ".robot-out/follows-cache.json";
const FOLLOWS_LIMIT = 1000;

function readCache(file) {
  try {
    if (!existsSync(file)) return [];
    const parsed = JSON.parse(readFileSync(file, "utf8"));
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function writeCache(file, names, limit) {
  try {
    mkdirSync(".robot-out", { recursive: true });
    writeFileSync(file, `${JSON.stringify(names.slice(0, limit), null, 2)}\n`);
  } catch {
    // caches are an optimisation; losing one is not worth failing a run
  }
}

/** Followed handles accumulated across runs — the feed only shows a slice each time. */
export function recallFollowsCache() {
  return readCache(FOLLOWS_CACHE);
}

export function rememberFollows(names) {
  if (!names.length) return recallFollowsCache();
  const merged = [...new Set([...recallFollowsCache(), ...names])];
  writeCache(FOLLOWS_CACHE, merged, FOLLOWS_LIMIT);
  return merged;
}

/**
 * Handles harvested from the Following video feed.
 *
 * That feed only contains posts from accounts the user follows, needs no modal,
 * and has never thrown a challenge — unlike the Following list, which serves a
 * CAPTCHA or an error. Each visit surfaces a slice of the follows, so results
 * are merged into a cache that grows run over run.
 */
export async function followingFeedAccounts(context, { scrolls = 12, log = () => {}, shot } = {}) {
  const page = await context.newPage();
  const seen = new Set();
  try {
    await page.goto("https://www.tiktok.com/following", { waitUntil: "domcontentloaded", timeout: 60000 });
    try {
      await page.waitForSelector("a[href*='/@']", { timeout: 30000 });
    } catch {
      log("  following feed did not render");
      return [];
    }
    await page.waitForTimeout(3000);

    const grab = async () => {
      const names = await page.$$eval("a[href*='/@']", (anchors) =>
        anchors
          .map((anchor) => (anchor.getAttribute("href") || "").match(/\/@([A-Za-z0-9._]+)/)?.[1])
          .filter(Boolean),
      );
      for (const name of names) if (!NOT_USERNAMES.has(name.toLowerCase())) seen.add(name);
    };

    await grab();
    for (let step = 0; step < scrolls; step += 1) {
      await page.keyboard.press("ArrowDown").catch(() => {});
      await page.waitForTimeout(1400);
      if (step % 3 === 2) await grab();
    }
    await grab();
    await shot?.(page, "following-feed");
    log(`  ${seen.size} followed account(s) from the Following feed`);
    return [...seen];
  } catch (error) {
    log(`  following feed failed: ${error.message}`);
    return [...seen];
  } finally {
    await page.close().catch(() => {});
  }
}

/**
 * Names seen live on earlier runs.
 *
 * TikTok throttles the LIVE feeds after a few automated visits, and once that
 * happens every feed returns "No LIVE streams for you yet" on a perfectly
 * signed-in page. Creators who were live yesterday are usually live again, so
 * the cache turns a throttled run into a working one.
 */
export function recallSeenLive() {
  return readCache(SEEN_FILE);
}

export function rememberSeenLive(names) {
  if (!names.length) return;
  writeCache(SEEN_FILE, [...new Set([...names, ...recallSeenLive()])], SEEN_LIMIT);
}

// Nav and product links that live under /@ or look like usernames but are not.
const NOT_USERNAMES = new Set(["live", "following", "foryou", "explore", "upload", "search"]);

/**
 * Harvest profile handles from one page.
 *
 * These pages hydrate client-side over the VPS tunnel, so a fixed sleep is a
 * race — it produced 42 names on one run and 0 on the next. Wait for the links
 * to actually exist instead, then give the list a moment to finish filling in.
 */
async function harvestHandles(page, url, { log, shot, headed }) {
  const response = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 60000 });
  if (!response?.ok()) {
    log(`  ${url} returned ${response?.status()}`);
    return [];
  }

  await waitOutChallenge(page, { headed, log });

  try {
    await page.waitForSelector("a[href*='/@']", { timeout: 30000 });
    await page.waitForTimeout(3000);
    await shot?.(page, `feed-${url.replace(/https?:\/\/[^/]+\//, "").replace(/\W+/g, "-").slice(0, 30)}`);
  } catch {
    await shot?.(page, "feed-empty");
    return []; // nothing rendered: an empty feed, not an error
  }

  const hrefs = await page.$$eval("a[href*='/@']", (anchors) =>
    anchors.map((anchor) => anchor.getAttribute("href")).filter(Boolean),
  );
  // Prefer explicit /@name/live links, then bare profile links.
  const ordered = [
    ...hrefs.filter((href) => /\/@[^/]+\/live/.test(href)),
    ...hrefs.filter((href) => !/\/@[^/]+\/live/.test(href)),
  ];

  const names = [];
  for (const href of ordered) {
    const name = (href.match(/\/@([A-Za-z0-9._]+)/) || [])[1];
    if (!name || NOT_USERNAMES.has(name.toLowerCase())) continue;
    if (!names.includes(name)) names.push(name);
  }
  return names;
}

/**
 * Handles the user maintains by hand, one per line, in a gitignored file.
 *
 * The complete following list cannot be scraped: opening the Following modal
 * triggers TikTok's slider CAPTCHA, which this robot will not solve. The home
 * sidebar only previews a handful of the 650 follows. So the reliable way to
 * say "watch these accounts" is to list them.
 */
export function listedFollows({ log = () => {} } = {}) {
  const file = ".robot-follows";
  try {
    if (!existsSync(file)) return [];
    const names = readFileSync(file, "utf8")
      .split("\n")
      .map((line) => line.trim().replace(/^@/, ""))
      .filter((line) => line && !line.startsWith("#"));
    if (names.length) log(`  ${names.length} account(s) from ${file}`);
    return names;
  } catch (error) {
    log(`  could not read ${file}: ${error.message}`);
    return [];
  }
}

/**
 * Accounts the signed-in user follows, as far as they can be seen without
 * tripping the CAPTCHA: the home sidebar preview. Partial by nature — pair it
 * with `listedFollows` for the full picture.
 */
export async function followedAccounts(context, { limit = 40, log = () => {}, shot, headed } = {}) {
  const page = await context.newPage();
  try {
    await page.goto("https://www.tiktok.com/", { waitUntil: "domcontentloaded", timeout: 60000 });
    await waitOutChallenge(page, { headed, log });
    try {
      await page.waitForSelector("a[href*='/@']", { timeout: 30000 });
    } catch {
      log("  home sidebar never rendered");
      return [];
    }

    await shot?.(page, "home-following-sidebar");

    // Deliberately not clicking "View all": that opens the Following modal,
    // which is where TikTok serves the slider CAPTCHA. The preview is partial
    // but free of challenges.

    const entries = await page.$$eval("a[href*='/@']", (anchors) =>
      anchors.map((anchor) => {
        const card = anchor.closest("li,div") || anchor;
        return {
          href: anchor.getAttribute("href") || "",
          live: /\bLIVE\b/.test(card.textContent || ""),
        };
      }),
    );

    const live = [];
    const rest = [];
    for (const entry of entries) {
      const name = (entry.href.match(/\/@([A-Za-z0-9._]+)/) || [])[1];
      if (!name || NOT_USERNAMES.has(name.toLowerCase())) continue;
      const bucket = entry.live ? live : rest;
      if (!live.includes(name) && !rest.includes(name)) bucket.push(name);
    }
    if (live.length) log(`  ${live.length} followed account(s) badged LIVE`);
    log(`  ${live.length + rest.length} followed account(s) total`);
    // Badged-live first: those are the ones worth checking first.
    return [...live, ...rest].slice(0, limit);
  } catch (error) {
    log(`  following list failed: ${error.message}`);
    return [];
  } finally {
    await page.close().catch(() => {});
  }
}

export async function discoverLiveUsernames(context, { limit = 25, log = () => {}, shot, headed } = {}) {
  const page = await context.newPage();
  const found = [];
  const add = (names) => {
    for (const name of names) {
      if (found.length >= limit) break;
      if (!found.includes(name)) found.push(name);
    }
  };

  // Search first: it is the source that keeps working when the feed does not.
  const sources = [...SEARCH_TERMS.map(searchUrl), ...FEEDS];
  try {
    for (const url of sources) {
      if (found.length >= limit) break;
      try {
        const names = await harvestHandles(page, url, { log, shot, headed });
        add(names);
        log(`  ${url.replace("https://www.tiktok.com", "")} -> ${names.length} name(s), ${found.length} total`);
      } catch (error) {
        log(`  ${url} failed: ${error.message}`);
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
export async function firstRecordable(client, usernames, { log = () => {}, maxChecks = 15 } = {}) {
  // Each check shells out to the vendor API on the server and takes a few
  // seconds, so cap the walk rather than grinding through 40+ leads.
  const shortlist = usernames.slice(0, maxChecks);
  for (const username of shortlist) {
    try {
      const status = await client.checkLive({ username });
      log(`  @${username}: ${status.can_record ? "RECORDABLE" : status.message}`);
      if (status.can_record) {
        rememberSeenLive([username]);
        return { username, status };
      }
    } catch (error) {
      log(`  @${username}: check failed (${error.message})`);
    }
  }
  if (usernames.length > shortlist.length) {
    log(`  (stopped after ${shortlist.length} of ${usernames.length} candidates)`);
  }
  return null;
}
