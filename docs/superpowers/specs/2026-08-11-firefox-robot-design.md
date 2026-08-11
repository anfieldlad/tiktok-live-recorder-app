# Firefox robot — end-to-end checker for the live path

Date: 2026-08-11
Status: approved, ready to implement

## Problem

The live recording path is the least testable part of this app and the least
covered. It needs a TikTok account that is live *right now*, a signed-in TikTok
session on the server, and network access to TikTok — none of which a unit test
or the existing Playwright spec can provide.

Three environment facts shape the whole design:

1. **TikTok and Instagram are blocked on this network.** They are reachable only
   through the SSH SOCKS proxy on `127.0.0.1:1080` that
   `~/dev/personal/wa-bypass` sets up. Firefox (profile `~/.proxy-firefox`, PAC
   file) is the browser wired for it.
2. **A local app instance therefore cannot record anything** — its own outbound
   yt-dlp/recorder/curl_cffi calls would be blocked. The production deployment
   on the VPS has unrestricted access, so the robot targets the deployed app.
   Its URL is read from `ROBOT_BASE_URL` or the gitignored `.env.robot`, never
   committed — the API is unauthenticated, so the address is worth as much as a
   credential.
3. **Playwright cannot drive the user's running Firefox.** It ships a patched
   Firefox build; pointing it at a live profile directory risks corruption and
   fails on the profile lock.

The user's requirement: they log into Firefox and do nothing else. The robot
supplies its own identity, network path, test subject, and verdict.

## Approach

A Node script, `npm run robot`, that reproduces the user's Firefox situation in
Playwright rather than reusing the browser itself:

- **Identity** — TikTok `session_ss` and Instagram `sessionid` are read out of a
  temporary copy of `~/.proxy-firefox/cookies.sqlite` (plus its `-wal`) and
  injected into the Playwright context with `addCookies`. The copy is deleted
  immediately. Cookie values are never logged, written to disk, or printed.
- **Network** — the browser launches with
  `proxy: { server: "socks5://127.0.0.1:1080", bypass: "<app host>,127.0.0.1,localhost" }`,
  mirroring the PAC: social domains through the tunnel, the app direct.
- **Oracle** — candidate live usernames scraped from TikTok are confirmed via the
  app's own `POST /recordings/check-live`, so imperfect scraping degrades to a
  skipped candidate rather than a false failure.

No new npm dependencies: Node 24 provides `node:sqlite`, and `@playwright/test`
1.57 is already a devDependency. One-time `npx playwright install firefox` is
required.

## Components

Five modules under `scripts/robot/`, each independently understandable:

| Module | Purpose | Depends on |
|---|---|---|
| `firefox-cookies.mjs` | Copy the cookie DB, extract TikTok/Instagram cookies, return them in Playwright format. Redacts on `toString`. | `node:sqlite`, `node:fs` |
| `app-client.mjs` | Typed wrapper over the production HTTP API (health, auth, check-live, recordings, relay, Instagram downloads). No browser. | `fetch` |
| `discover-live.mjs` | Candidate live usernames: TikTok feeds, the app's own history, and a cross-run cache. | Playwright |
| `discover-instagram.mjs` | Pick a post from the signed-in Instagram feed rather than hardcoding a shortcode. | Playwright |
| `run.mjs` | Orchestrates the seven steps, owns the report, exit code, and cleanup. | the other three |

## The run

Each step reports `PASS` / `FAIL` / `SKIP` independently and the report prints at
the end regardless of outcome.

1. **Preflight** — SOCKS tunnel listening on 1080; `GET /tiktok/health` is 200;
   Playwright Firefox present. A missing tunnel aborts with the `proxy-start.sh`
   hint instead of failing later in a confusing way.
2. **Session sync** — read `GET /tiktok/health/details`; if
   `cookies_configured` is false, `POST /tiktok/auth/tiktok-cookies` with the
   extracted `session_ss` and confirm it flips to true.
3. **Discovery** — three sources, in order: the TikTok LIVE feeds, accounts the
   app already tracks (watch jobs and recording history), and a cache of
   creators seen live on earlier runs (`.robot-out/seen-live.json`). Each
   candidate is confirmed through `check-live` until one returns
   `can_record: true`; `--user NAME` skips discovery entirely.

   The extra sources are not belt-and-braces. TikTok stops populating the LIVE
   feeds after a handful of automated visits — every feed, including the curated
   category tabs, answers "No LIVE streams for you yet" on a page that is
   provably still signed in (its `passport/web/account/info/` endpoint returns
   200 for the account). Observed directly: 9 accounts at 09:05, 0 from 09:35
   onward, same browser and exit IP, while the app confirmed two of those
   accounts were live and recordable at 09:38.
4. **Record through the UI** — drive the real page: fill `#record-source` and
   `#record-duration` (20s), submit, wait for the job card to reach a terminal
   phase. Asserts status `finished` with a `file_path`.
5. **Download and clean up** — fetch the file URL, assert non-zero length and an
   MP4 `ftyp` box in the first bytes, then `DELETE /tiktok/recordings/{id}`.
6. **Relay** — `GET /tiktok/live/stream?username=…`, read ~256 KB, abort the
   request, assert the MP4 signature. This path has no coverage today.
7. **Instagram spot-check** — the server session is already configured; download
   one public reel and assert bytes. The app deletes the file server-side on its
   own after the download.

## Failure handling

- **No live user found is a SKIP, not a FAIL.** That is the normal state for
  most of the day, and a checker that cries wolf gets ignored. Steps 4–6 skip
  with a clear reason.
- Every step has a hard timeout so a hung relay or stalled page cannot wedge the
  run.
- Transient network failures retry with backoff before being called a failure; a
  dropped connection should not read as "the app is broken". Only safe methods
  retry — re-sending `POST /recordings` would start a second recording.
- On failure: step name, HTTP status or selector that broke, and a screenshot
  written to `.robot-out/` (gitignored).
- The recording job is deleted in a `finally`, so an assertion failure mid-flow
  still leaves the server clean.
- The server session stays configured after the run — that is a fix to a real
  gap (`cookies_configured: false` in production today), not test residue.

## Security constraints

- Cookie values never appear in logs, the report, screenshots, or any file. The
  cookie bundle carries a `toString` that renders `[redacted]`.
- The temporary copy of the cookie database is removed in a `finally` block.
- The robot only ever writes the session to the app's own auth endpoint over
  HTTPS.
- Reading `~/.proxy-firefox/cookies.sqlite` triggers a permission prompt from
  the Claude Code classifier. That prompt is expected and is the only
  interaction the run requires.

## Out of scope

- Local-app testing, which would require adding outbound SOCKS proxy support to
  `post_download_service`, `live_status_service`, and the recorder invocation.
  Deferred deliberately; revisit if local runs become useful.
- Authentication for the production API. The robot inherits the app's current
  unauthenticated posture; when auth lands, `app-client.mjs` is the single place
  that needs the credential.
- Turning any of this into a CI job. Live streams are not a fixture.
