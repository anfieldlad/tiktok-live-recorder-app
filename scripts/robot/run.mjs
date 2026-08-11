#!/usr/bin/env node
/**
 * End-to-end robot for the deployed saver app.
 *
 * Borrows the user's Firefox session and network path, finds a TikTok account
 * that is live right now, and drives the whole record -> download -> relay
 * cycle against production, then reports.
 *
 *   npm run robot                 # full run
 *   npm run robot -- --headed     # watch it work
 *   npm run robot -- --skip-ig    # TikTok only
 *
 * Nobody live is a SKIP, not a failure: that is the normal state most of the
 * day, and a checker that cries wolf gets ignored.
 */

import { mkdirSync, writeFileSync } from "node:fs";
import { connect } from "node:net";
import { join } from "node:path";

import { AppClient, DEFAULT_BASE_URL, looksLikeMp4 } from "./app-client.mjs";
import { launchFirefox, SOCKS_PROXY } from "./browser.mjs";
import { discoverInstagramPost } from "./discover-instagram.mjs";
import { discoverLiveUsernames, firstRecordable } from "./discover-live.mjs";
import { readSocialCookies } from "./firefox-cookies.mjs";

const OUT_DIR = ".robot-out";
const RECORD_SECONDS = Number(process.env.ROBOT_RECORD_SECONDS ?? 20);
const headed = process.argv.includes("--headed");
const skipInstagram = process.argv.includes("--skip-ig");

class Skip extends Error {}
const skip = (reason) => {
  throw new Skip(reason);
};

const results = [];
const log = (message) => process.stdout.write(`${message}\n`);

async function step(name, fn, ctx) {
  const started = Date.now();
  log(`\n▶ ${name}`);
  try {
    const detail = await fn(ctx);
    results.push({ name, status: "PASS", detail: detail ?? "", ms: Date.now() - started });
    log(`  ✓ ${detail ?? "ok"}`);
  } catch (error) {
    const status = error instanceof Skip ? "SKIP" : "FAIL";
    results.push({ name, status, detail: error.message, ms: Date.now() - started });
    log(`  ${status === "SKIP" ? "–" : "✗"} ${error.message}`);
    if (status === "FAIL" && ctx.page) {
      const shot = join(OUT_DIR, `${name.replace(/\W+/g, "-").toLowerCase()}.png`);
      await ctx.page.screenshot({ path: shot, fullPage: true }).catch(() => {});
      log(`    screenshot: ${shot}`);
    }
  }
}

function tcpOpen(host, port, timeoutMs = 3000) {
  return new Promise((resolve) => {
    const socket = connect({ host, port });
    const done = (value) => {
      socket.destroy();
      resolve(value);
    };
    socket.setTimeout(timeoutMs);
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
  });
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function waitForTerminalJob(client, jobId, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await client.getRecording(jobId);
    if (["finished", "failed", "stopped"].includes(last.status)) return last;
    await sleep(3000);
  }
  return last;
}

async function main() {
  mkdirSync(OUT_DIR, { recursive: true });
  const client = new AppClient();
  const ctx = { client, browser: null, context: null, page: null, username: null, jobId: null };

  log(`Robot target: ${DEFAULT_BASE_URL}`);
  log(`SOCKS proxy:  ${SOCKS_PROXY}`);

  await step("Preflight", async () => {
    const [host, port] = SOCKS_PROXY.replace(/^socks5h?:\/\//, "").split(":");
    if (!(await tcpOpen(host, Number(port)))) {
      throw new Error(`no SOCKS tunnel on ${host}:${port} — run ~/dev/personal/wa-bypass/proxy-start.sh`);
    }
    const health = await client.health();
    if (health?.status !== "ok") throw new Error(`app health returned ${JSON.stringify(health)}`);
    return `tunnel up, app healthy at ${DEFAULT_BASE_URL}`;
  }, ctx);

  await step("Session sync", async () => {
    const bundle = readSocialCookies();
    ctx.cookies = bundle.playwrightCookies;
    if (!bundle.sessionSs) throw new Error("no TikTok session_ss in the Firefox profile — log in there first");

    const before = await client.healthDetails();
    if (before.cookies_configured) {
      return `server already has a TikTok session (${bundle.counts.tiktok} cookies available)`;
    }
    await client.saveTikTokSession(bundle.sessionSs);
    const after = await client.healthDetails();
    if (!after.cookies_configured) throw new Error("server still reports cookies_configured=false after upload");
    return "uploaded the TikTok session from Firefox; server now signed in";
  }, ctx);

  await step("Discover a live account", async () => {
    const launched = await launchFirefox({
      cookies: ctx.cookies ?? [],
      bypassHosts: [new URL(DEFAULT_BASE_URL).hostname],
      headless: !headed,
    });
    ctx.browser = launched.browser;
    ctx.context = launched.context;
    ctx.close = launched.close;

    const candidates = await discoverLiveUsernames(ctx.context, { log });
    if (!candidates.length) skip("TikTok's LIVE feed returned no accounts");

    const hit = await firstRecordable(client, candidates, { log });
    if (!hit) skip(`none of ${candidates.length} candidates were recordable right now`);
    ctx.username = hit.username;
    return `@${hit.username} is live and recordable`;
  }, ctx);

  await step("Record through the UI", async () => {
    if (!ctx.username) skip("no live account from the previous step");

    const before = new Set((await client.listRecordings()).map((job) => job.id));
    const page = await ctx.context.newPage();
    ctx.page = page;
    await page.goto(`${DEFAULT_BASE_URL}/`, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.fill("#record-source", ctx.username);
    await page.fill("#record-duration", String(RECORD_SECONDS));
    await page.getByRole("button", { name: "Start recording" }).click();

    // The page posts to the API; find the job it created.
    let job = null;
    const deadline = Date.now() + 90000;
    while (Date.now() < deadline && !job) {
      await sleep(2000);
      job = (await client.listRecordings()).find((item) => !before.has(item.id)) ?? null;
    }
    if (!job) {
      const notice = await page.locator("#record-notice").innerText().catch(() => "");
      throw new Error(`no recording job was created. UI said: ${notice.slice(0, 200)}`);
    }
    ctx.jobId = job.id;
    log(`  job ${job.id} created, recording ${RECORD_SECONDS}s…`);

    const final = await waitForTerminalJob(client, job.id, (RECORD_SECONDS + 150) * 1000);
    if (final.status !== "finished") {
      throw new Error(`job ended as ${final.status}: ${final.error ?? final.progress_message}`);
    }
    if (!final.file_path) throw new Error("job finished but has no file_path");
    return `recorded ${final.file_name} (${final.file_size_bytes ?? "?"} bytes)`;
  }, ctx);

  await step("Download and clean up", async () => {
    if (!ctx.jobId) skip("no recording job from the previous step");

    const response = await ctx.client.downloadRecording(ctx.jobId);
    if (!response.ok) throw new Error(`download returned ${response.status}`);
    const body = Buffer.from(await response.arrayBuffer());
    if (body.length === 0) throw new Error("download returned an empty body");
    if (!looksLikeMp4(body.subarray(0, 32))) {
      throw new Error(`downloaded file is not an MP4 (first bytes: ${body.subarray(0, 8).toString("hex")})`);
    }
    // The server deletes the file and the job once it has been downloaded.
    const stillThere = (await client.listRecordings()).some((job) => job.id === ctx.jobId);
    if (stillThere) await client.deleteRecording(ctx.jobId).catch(() => {});
    return `downloaded ${(body.length / 1024 / 1024).toFixed(2)} MB, valid MP4, server cleaned up`;
  }, ctx);

  await step("Live relay", async () => {
    if (!ctx.username) skip("no live account from an earlier step");
    const sample = await client.sampleLiveRelay(ctx.username);
    if (sample.bytes === 0) throw new Error("relay produced no bytes");
    if (!looksLikeMp4(sample.head)) {
      throw new Error(`relay output is not MP4 (first bytes: ${Buffer.from(sample.head).subarray(0, 8).toString("hex")})`);
    }
    return `relayed ${(sample.bytes / 1024).toFixed(0)} KB of valid MP4, then hung up`;
  }, ctx);

  await step("Instagram download", async () => {
    if (skipInstagram) skip("--skip-ig");
    if (!ctx.context) skip("no browser context");

    const postUrl = await discoverInstagramPost(ctx.context, { log });
    if (!postUrl) skip("could not find a post in the Instagram feed");

    const result = await client.downloadInstagram(postUrl);
    if (!result.files?.length) throw new Error("download reported no files");

    const fileResponse = await fetch(client.resolve(result.file_urls[0]), {
      signal: AbortSignal.timeout(120000),
    });
    if (!fileResponse.ok) throw new Error(`file fetch returned ${fileResponse.status}`);
    const bytes = (await fileResponse.arrayBuffer()).byteLength;
    if (bytes === 0) throw new Error("downloaded Instagram file was empty");
    return `${result.files.length} file(s) from ${postUrl}, first is ${(bytes / 1024).toFixed(0)} KB`;
  }, ctx);

  if (ctx.close) await ctx.close();

  const width = Math.max(...results.map((row) => row.name.length));
  log("\n──────── report ────────");
  for (const row of results) {
    const mark = { PASS: "✓", FAIL: "✗", SKIP: "–" }[row.status];
    log(`${mark} ${row.name.padEnd(width)}  ${String(row.ms).padStart(6)}ms  ${row.detail}`);
  }
  const failed = results.filter((row) => row.status === "FAIL");
  const skipped = results.filter((row) => row.status === "SKIP");
  log(
    `\n${results.length - failed.length - skipped.length} passed, ${failed.length} failed, ${skipped.length} skipped`,
  );
  writeFileSync(join(OUT_DIR, "report.json"), `${JSON.stringify(results, null, 2)}\n`);
  process.exit(failed.length ? 1 : 0);
}

main().catch(async (error) => {
  log(`\nrobot crashed: ${error.stack ?? error.message}`);
  process.exit(2);
});
