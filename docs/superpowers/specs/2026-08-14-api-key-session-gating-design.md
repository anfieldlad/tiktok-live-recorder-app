# Gating the session behind an API key — design

> **Status:** designed, not implemented. Written 2026-08-14 to be executed in a
> later session. Generate the task-by-task plan from this with
> `superpowers:writing-plans` before touching code.

**Goal:** stop a stranger who knows the URL from spending the TikTok and Instagram
sessions stored on the server — without a login wall, without breaking the existing
Android client, and without an Android release.

## The problem

The server has never checked authentication. Every route is open to anyone who knows
the URL, and the rebrand to `/stillhere` made that URL easier to guess. The server
holds **logged-in TikTok and Instagram sessions**, so the exposure is not that someone
reads the register — it is that someone acts as the account holder.

An anonymous caller can today:

| Action | Endpoint |
|---|---|
| Overwrite or wipe the saved session | `POST` / `DELETE /auth/tiktok-cookies`, `/instagram/auth/cookies` |
| Export the session from a browser profile | `POST /auth/import-browser/{browser}` |
| Launch a browser on the VPS | `POST /auth/login-browser/{browser}/start` |
| Fetch any post **as the account holder** | `POST /downloads`, `POST /instagram/downloads` |
| Check live status using the session | `POST /recordings/check-live` |
| Start recordings and relays | `POST /recordings`, `POST /watch-recordings`, `/live/stream` |

There is no rate limiting anywhere.

## What is already built

The Android client was written in anticipation of this and needs **no code change**:

- `SettingsRepo` persists an API key (`KEY_API_KEY`, DataStore).
- `ApiClient.applyApiKey()` attaches `X-API-Key` to all seven request types.
- `toApiException` already renders 401 as *"Unauthorized — check the backend URL and
  API key in Settings."*
- The Settings screen has the field, labelled **"API key (optional)"**, hinting
  *"only set it if your server requires one."*

The single manual step is pasting the key into Android Settings once. See
[Rollout](#rollout) — order matters.

## The model: degrade, don't block

The rule the design encodes, in the owner's words:

> Reading and writing stays open. But if it's not logged in, it can't use my session.

So the key does **not** gate features. It gates *whose identity a fetch runs as*.
An anonymous visitor can still use the app; their requests simply run without the
stored cookies, so they reach public content only.

This is cheap to build because it is not a new code path. `PostDownloadService`,
`InstagramDownloadService` and `LiveStatusService` all already accept
`cookie_service=None` and fall back to cookie-less operation — that is exactly what
happens today when no session is saved. The change is choosing, per request, whether
to hand the service the real cookie service or `None`.

### No login page, no cookie

Worth stating because it removes most of the expected complexity: **everything gated
is called by `fetch()`; nothing gated is ever a plain `<a href>`.** Session management
and session-spending actions are all XHR. Media file links — the only plain
navigations, and the only thing that cannot carry a header — stay open.

A header-only scheme therefore covers every case. No signed cookie, no login
template, no CSRF surface, no session expiry, no redirect wall. The site stays as
publicly loadable as it is now.

## Route tiers

**Tier 1 — requires the key (401 without it).** The session itself.

- `POST` / `DELETE /auth/tiktok-cookies`
- `POST` / `DELETE /instagram/auth/cookies`
- `POST /auth/import-browser/{browser_name}` and the Instagram equivalent
- `POST /auth/login-browser/{browser_name}/start`, `/capture`, `/close`, and the
  Instagram equivalents

**Tier 2 — open, but runs cookie-less without the key.** Session-spending actions.

- `POST /downloads`, `POST /instagram/downloads`
- `POST /recordings`, `POST /watch-recordings`
- `POST /recordings/check-live`
- `/live/stream`

**Tier 3 — fully open, unchanged.** Pages, static assets, favicon, `/health`,
`/health/details`, `GET /recordings`, `GET /watch-recordings`, `GET /downloads`,
every media file URL, and all `DELETE` routes.

### Status endpoints are a deliberate special case

`GET /auth/status` and `GET /instagram/auth/status` stay **open**, so the Sessions
drawer can render honestly for an anonymous visitor rather than erroring. They return
`{configured, cookie_file}`; `cookie_file` is a filesystem path and should be
**redacted to `null` for unauthenticated callers**, matching how `/health/details`
already redacts in production.

`GET /auth/login-browser/status` likewise stays open — it reports only whether guided
login is supported on this platform.

## Implementation shape

Two FastAPI dependencies, both reading `X-API-Key`:

- `require_key` — raises 401 when the header is absent or wrong. Applied to Tier 1.
- `session_allowed() -> bool` — never raises. Tier 2 routes use it to pass either the
  real cookie service or `None` into their service call.

The key lives in `Settings` as `api_key: str = ""` (env `API_KEY`), read from
`/opt/ttl-downloader/.env` in production.

**When `API_KEY` is empty, enforcement is off entirely** — `require_key` passes and
`session_allowed` returns `True`. This keeps local development and the test suite
working unchanged, and means a missing env var degrades to today's behaviour rather
than locking everyone out.

Comparison must use `hmac.compare_digest`, not `==`.

Tier 2 currently receives its cookie service via constructor injection, so the service
methods need a per-call way to opt out. Prefer threading a `use_session: bool`
parameter through the call rather than mutating shared service state — the services
are long-lived singletons on `app.state` and must stay safe under concurrent requests.

### Web UI

- `app-common.js` grows an `apiFetch(path, init)` wrapper that attaches `X-API-Key`
  from `localStorage` when present. All existing `fetch(appPath(...))` calls move to it.
- The Sessions drawer gains a key field: paste once, stored in `localStorage`,
  cleared by a "Forget key" button.
- A 401 must surface the same way Android does — a notice pointing at the key field,
  not a raw error.
- The masthead session dot should reflect *authorisation*, not just whether a session
  file exists, so an anonymous visitor is not told the session is "ready" when they
  cannot use it.

## Rollout

Order matters; getting it wrong causes an Android outage.

1. Generate a key (`openssl rand -hex 32`).
2. **Paste it into Android Settings first** and confirm the app still works against the
   still-unenforcing server.
3. Add `API_KEY=…` to `/opt/ttl-downloader/.env`.
4. Deploy the code and restart. Enforcement begins the moment the env var is present.
5. Paste the key into the web UI's Sessions drawer.
6. Verify: Tier 1 returns 401 without the header and 200 with it; Tier 2 succeeds both
   ways but only returns private content with the key; Tier 3 is untouched.

Rollback is removing `API_KEY` from `.env` and restarting — enforcement switches off
without a code change.

## Accepted risks

Explicitly chosen by the owner after being shown the consequences. Recorded so a
later reader does not mistake them for oversights.

1. **A stranger with the link can delete saved recordings and downloads.** `DELETE`
   routes stay open. Locking them was offered and declined.
2. **A stranger can fill the disk** with public (cookie-less) downloads. The volume is
   19 GB with ~6 GB used, and there is no rate limit. A disk budget and per-IP limit
   were offered and declined. Note the VPS has a prior incident of a full disk taking
   the box down (`bad-core` crash loop, 2026-08-10).
3. **A stranger can see what has been saved** and download the media, since the
   register list and file URLs stay open.

If any of these later prove uncomfortable, each is an independent, additive change —
none of them require revisiting this design.

## Testing

The suite runs with `API_KEY` unset, so existing tests keep passing untouched. New
tests set it explicitly:

- Tier 1 without the header → 401; with a wrong key → 401; with the right key → 200.
- Tier 2 without the header → 200, and the service is called with no cookie service.
- Tier 2 with the header → 200, and the service is called *with* the cookie service.
- Tier 3 without the header → 200 (guards against over-gating).
- `GET /auth/status` unauthenticated → 200 with `cookie_file` redacted to `null`.
- With `API_KEY` unset, a Tier 1 route → 200 (enforcement genuinely off).

The Tier 2 assertions are the ones that matter most: they are what distinguish this
design from simply blocking the endpoint, and they are easy to get silently wrong.

## Out of scope

Rate limiting, disk quotas, gating deletes, multi-user accounts, key rotation, and
per-client keys. All are additive later.
