/**
 * Borrow the user's logged-in TikTok/Instagram sessions from Firefox.
 *
 * Playwright ships its own patched Firefox and cannot attach to a running one,
 * so instead of driving the user's browser we lift its cookies into a Playwright
 * context. Firefox keeps cookies in SQLite with a write-ahead log, and the
 * browser is normally running with the database locked, so we work on a copy of
 * the .sqlite plus its -wal/-shm sidecars and delete the copy afterwards.
 *
 * Cookie values never leave this module in printable form: the returned bundle
 * renders as [redacted] if anyone logs it.
 */

import { DatabaseSync } from "node:sqlite";
import { copyFileSync, existsSync, mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const SIDECARS = ["", "-wal", "-shm"];

// Firefox sameSite: 0 = none, 1 = lax, 2 = strict.
const SAME_SITE = { 0: "None", 1: "Lax", 2: "Strict" };

// 9999-12-31, the largest expiry Playwright accepts.
const MAX_EXPIRY_SECONDS = 253402300799;
// A seconds timestamp only reaches 1e11 in the year 5138, so anything above
// that is milliseconds — which is what current Firefox actually writes here,
// despite the column being documented as seconds.
const MILLISECOND_THRESHOLD = 1e11;

function toUnixSeconds(raw) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) return -1; // session cookie
  const seconds = value > MILLISECOND_THRESHOLD ? Math.floor(value / 1000) : Math.floor(value);
  return Math.min(seconds, MAX_EXPIRY_SECONDS);
}

export const DEFAULT_PROFILE = join(
  process.env.HOME ?? "",
  ".proxy-firefox",
);

/**
 * @param {string} profileDir Firefox profile directory to read.
 * @returns {{playwrightCookies: object[], sessionSs: string|null, instagramSessionId: string|null, counts: object, toString: () => string}}
 */
export function readSocialCookies(profileDir = DEFAULT_PROFILE) {
  const source = join(profileDir, "cookies.sqlite");
  if (!existsSync(source)) {
    throw new Error(`no cookies.sqlite in ${profileDir} — is that the right Firefox profile?`);
  }

  const workDir = mkdtempSync(join(tmpdir(), "robot-cookies-"));
  try {
    for (const suffix of SIDECARS) {
      const from = `${source}${suffix}`;
      if (existsSync(from)) copyFileSync(from, join(workDir, `cookies.sqlite${suffix}`));
    }

    // Opened read-write on purpose: SQLite has to replay the WAL to see cookies
    // written since the last checkpoint. It is a throwaway copy.
    const db = new DatabaseSync(join(workDir, "cookies.sqlite"));
    let rows;
    try {
      rows = db
        .prepare(
          `SELECT host, name, value, path, isSecure, isHttpOnly, sameSite, expiry
             FROM moz_cookies
            WHERE host LIKE '%tiktok.com' OR host LIKE '%instagram.com'`,
        )
        .all();
    } finally {
      db.close();
    }

    const playwrightCookies = rows.map((row) => ({
      name: String(row.name),
      value: String(row.value),
      domain: String(row.host),
      path: String(row.path || "/"),
      expires: toUnixSeconds(row.expiry),
      httpOnly: Boolean(row.isHttpOnly),
      secure: Boolean(row.isSecure),
      sameSite: SAME_SITE[row.sameSite] ?? "Lax",
    }));

    const pick = (hostFragment, name) =>
      rows.find((row) => String(row.host).includes(hostFragment) && row.name === name)?.value ?? null;

    // The recorder writes these keys out as cookie names, so send the real
    // ones. msToken rotates and the recorder fetches its own; the rest is UI
    // noise that would only bloat the file.
    const NOISE = new Set([
      "msToken", "perf_feed_cache", "tiktok_webapp_theme", "tiktok_webapp_theme_source",
      "delay_guest_mode_vid", "guest_mode_flag", "_ttp", "waforigin_id",
      "waforiginalreid", "_waftokenid",
    ]);
    const tiktokCookieMap = {};
    for (const row of rows) {
      const host = String(row.host);
      const name = String(row.name);
      if (!host.includes("tiktok.com") || NOISE.has(name) || !row.value) continue;
      tiktokCookieMap[name] = String(row.value);
    }

    const bundle = {
      playwrightCookies,
      tiktokCookieMap,
      // There is no session_ss cookie on TikTok; sessionid is the real one.
      sessionSs: pick("tiktok.com", "sessionid") ?? pick("tiktok.com", "sessionid_ss"),
      instagramSessionId: pick("instagram.com", "sessionid"),
      counts: {
        tiktok: rows.filter((row) => String(row.host).includes("tiktok.com")).length,
        instagram: rows.filter((row) => String(row.host).includes("instagram.com")).length,
      },
    };
    // Anything that stringifies this bundle gets nothing useful.
    Object.defineProperty(bundle, "toString", {
      value: () => "[redacted cookie bundle]",
      enumerable: false,
    });
    return bundle;
  } finally {
    rmSync(workDir, { recursive: true, force: true });
  }
}
