# AGENTS.md

Guidance for AI coding agents working in this repository.

For production server details (SSH, nginx, systemd, deploy steps, troubleshooting) see [SSH.md](SSH.md).

## Project Overview

This is a local FastAPI application for saving TikTok and Instagram media. It ships
as two **sister apps** in one repository and one process: a TikTok saver (record
Live, auto-record, download public posts) and an Instagram saver (download posts,
reels, carousels, stories, highlights) mounted under the `/instagram` path prefix.
They share layout, CSS, backend infrastructure, and deployment, but each has its
own branding, accent theme, and session/cookie flow. A top-bar switcher links
between them. Which app renders is driven by a `platform` value (`tiktok` |
`instagram`) passed into the templates.

Primary code areas:

- `app/main.py` wires the FastAPI app, all services (TikTok + Instagram), routes, templates, static files, and health endpoints. `render_dashboard(..., platform=...)` selects per-app context.
- `app/api/` contains the TikTok API routers (auth, downloads, recordings).
- `app/services/` contains shared and TikTok services: app state, recorder integration, browser login, cookies, file handling, live status, and watch logic.
- `app/services/post_download_service.py` contains TikTok post download integration through `yt-dlp` and the picture-post fallback.
- `app/services/chromium_cookies.py` is the shared, domain-parameterized Chromium cookie reader used by both the TikTok and Instagram cookie services.
- `app/instagram/` is the Instagram sister app: `api/` (`/instagram/downloads`, `/instagram/auth`), `services/` (`instagram_download_service.py` with the gallery-dl/yt-dlp engine routing, `instagram_cookie_service.py`, `instagram_browser_login_service.py`), and `router.py`.
- `app/models/` contains shared data models.
- `app/templates/` contains Jinja templates (TikTok pages, `instagram_download.html`, and `_session_panel.html` / `_ig_session_panel.html`).
- `app/static/css/` and `app/static/js/` contain frontend assets.
- `tests/` contains Python unit/integration tests.

Runtime/generated directories may exist locally:

- `data/`
- `logs/`
- `output/`
- `vendor/`

Do not treat those as source unless a task explicitly asks for changes there.

## Setup

Use Python 3.11+.

Install app dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The actual recording workflow expects the upstream recorder engine under:

```text
vendor/tiktok-live-recorder
```

Tests do not require a real upstream checkout because they create temporary settings and paths.

## Run

Start the local app:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Test

Run the existing test suite with:

```bash
.venv/bin/python -m unittest discover -s tests
```

Run browser e2e tests with:

```bash
npm run test:e2e
```

Drive the **deployed** app end to end with the Firefox robot (`scripts/robot/`):

```bash
npm run robot
```

It borrows the TikTok/Instagram sessions from the `~/.proxy-firefox` Firefox
profile, routes social traffic through the SSH SOCKS proxy on `127.0.0.1:1080`
(TikTok/Instagram are blocked on this network — see `~/dev/personal/wa-bypass`),
finds an account that is live right now, then records, downloads, relays, and
cleans up. Nobody live is reported as SKIP, not a failure. Flags: `--headed` to
watch, `--skip-ig` for TikTok only, `--user NAME` to test one account and skip
discovery — useful because TikTok stops populating its LIVE feeds after a few
automated visits. Design notes:
`docs/superpowers/specs/2026-08-11-firefox-robot-design.md`.

The target URL is **not** in the repo: the robot reads `ROBOT_BASE_URL` from the
environment or from a gitignored `.env.robot`, falling back to
`http://127.0.0.1:8000`. The deployed API is unauthenticated, so treat its
address like a credential — same rule `SSH.md` applies to server details.

If adding behavior that changes routing, service state, store recovery, diagnostics, downloads, or watch/record flows, add or update tests under `tests/`.

## Coding Guidelines

- Follow the existing small-service structure in `app/services/` instead of adding large cross-cutting modules.
- Keep API behavior explicit and return structured errors where existing routes do.
- Prefer `pathlib.Path` for filesystem paths.
- Preserve the current pattern of dependency wiring through `create_app()` and `app.state`.
- Avoid importing the upstream recorder directly into request handlers; keep recorder integration inside services.
- Keep frontend changes split between shared files and page-specific files:
  - shared helpers in `app/static/js/app-common.js`
  - shared styles in `app/static/css/app.css` (Instagram theme via `body[data-app="instagram"]`)
  - TikTok session logic in `app/static/js/session-panel.js`; Instagram in `app/static/js/ig-session-panel.js`
  - live page logic in `record-page.js` or `watch-page.js`
  - TikTok post download logic in `download-page.js`; Instagram in `instagram-download-page.js`
- Keep the sister-app boundary clean: Instagram code lives under `app/instagram/`; don't entangle it with the TikTok routers/services. Reuse shared helpers (e.g. `app/services/chromium_cookies.py`) rather than duplicating.
- Per-app UI is selected by the `platform` template variable; route page handlers through `render_dashboard(..., platform=...)`.
- Do not commit local recordings, cookies, logs, temporary job files, virtualenvs, or cloned vendor code.

## Safety Notes

This app launches recording/download subprocesses (`yt-dlp`, `gallery-dl`, the
recorder) and handles local cookies/session data. Be careful when changing:

- `CookieService`, `InstagramCookieService`, and the shared `chromium_cookies` helper
- `BrowserLoginService` and `InstagramBrowserLoginService`
- `RecorderService`
- the download services (`post_download_service.py`, `instagram_download_service.py`) — they shell out to `yt-dlp`/`gallery-dl` with user-supplied URLs and a temp cookie file
- file deletion after downloads — Instagram and finished recordings are deleted once downloaded (`cleanup_file_after_download`, `cleanup_download_artifacts`)
- job/watch store recovery and persistence

TikTok and Instagram sessions are stored separately (`recorder_cookies.json`
keyed on `session_ss`; `data/instagram_cookies.json` keyed on `sessionid`).
Avoid exposing cookie/session values in logs, diagnostics, errors, or templates.

Three small modules exist to keep that easy — use them instead of hand-rolling:

- `app/services/url_guard.py` — every client-supplied URL that reaches a
  subprocess or the vendor HTTP client goes through `validate_tiktok_url`, and
  every URL that came back from a third party goes through
  `ensure_public_http_url` before we fetch it.
- `app/services/secure_files.py` — anything holding session material is written
  0600 at creation time (`write_private_text` / `write_private_temp_text`).
- `app/services/redaction.py` — subprocess output that is served back over the
  API passes through `redact_sensitive` first; the raw text stays in the log
  files under `logs/`.

The API itself is still unauthenticated — do not add endpoints that widen what
an anonymous caller can reach until that is fixed.

## Deploy

Production runs under `ROOT_PATH=/tiktok` behind nginx, so the Instagram saver is
served at `/tiktok/instagram`. Deploy by pushing to `main` and pulling on the
server (`git pull` + `pip install -r requirements.txt` + restart). New runtime
dependencies (e.g. `gallery-dl`) must be installed before restart. Full server
details are in [SSH.md](SSH.md).
