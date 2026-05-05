# AGENTS.md

Guidance for AI coding agents working in this repository.

For production server details (SSH, nginx, systemd, deploy steps, troubleshooting) see [SSH.md](SSH.md).

## Project Overview

This is a local FastAPI application for saving TikTok media. The app provides browser UI pages for recording TikTok Live streams, watching accounts until they go live, and downloading public TikTok video or picture posts.

Primary code areas:

- `app/main.py` wires the FastAPI app, services, routes, templates, static files, and health endpoints.
- `app/api/` contains API routers.
- `app/services/` contains app state, recorder integration, browser login, cookies, file handling, live status, and watch logic.
- `app/services/post_download_service.py` contains post download integration through `yt-dlp` and the picture-post fallback.
- `app/models/` contains shared data models.
- `app/templates/` contains Jinja templates.
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

If adding behavior that changes routing, service state, store recovery, diagnostics, downloads, or watch/record flows, add or update tests under `tests/`.

## Coding Guidelines

- Follow the existing small-service structure in `app/services/` instead of adding large cross-cutting modules.
- Keep API behavior explicit and return structured errors where existing routes do.
- Prefer `pathlib.Path` for filesystem paths.
- Preserve the current pattern of dependency wiring through `create_app()` and `app.state`.
- Avoid importing the upstream recorder directly into request handlers; keep recorder integration inside services.
- Keep frontend changes split between shared files and page-specific files:
  - shared helpers in `app/static/js/app-common.js`
  - shared session logic in `app/static/js/session-panel.js`
  - live page logic in `record-page.js` or `watch-page.js`
  - post download logic in `download-page.js`
  - shared styles in `app/static/css/app.css`
- Do not commit local recordings, cookies, logs, temporary job files, virtualenvs, or cloned vendor code.

## Safety Notes

This app can launch recording subprocesses and handle local cookies/session data. Be careful when changing:

- `CookieService`
- `BrowserLoginService`
- `RecorderService`
- file deletion after downloads
- job/watch store recovery and persistence

Avoid exposing cookie/session values in logs, diagnostics, errors, or templates.
