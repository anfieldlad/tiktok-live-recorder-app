# Open items — server

Everything left over from the concurrent-downloads and API-key work, in the
order it should be picked up. Written 2026-08-16.

**State of play:** branch `feat/concurrent-downloads-and-api-key`, 18 commits
ahead of `main`, **not pushed and not deployed**. Production is still running
the previous revision. Nothing below has happened on the VPS.

Companion doc: `../../tiktok-live-recorder-android/docs/open-items.md`. The two
repos are coupled — three items here cannot be closed until the Android app
ships.

---

## 1. Ship first — independent of everything else

### 1.1 Raise `proxy_read_timeout` on `/stillhere/` and `/tiktok/`

**This is a live bug, not a new feature.** Neither nginx block sets
`proxy_read_timeout`, so the 60-second default applies: *any download slower
than a minute already returns 504 today*, and reads to the user as a failed
download. The UI has been anticipating it for a while — the elapsed counter says
"taking a bit longer than usual" at 30s.

The pattern already exists on the same box: `/breaking-bad/` sets
`proxy_read_timeout 86400`.

Worth doing before the job model is deployed, because the synchronous door holds
a connection open for the whole download.

Commands and the two-mount nginx layout are in `SSH.md`.

### 1.2 Deploy the branch

Deploy checklist is in `SSH.md`. Two things to know:

- `data/downloads.json` needs no migration. Rows written before the job model
  lack the new fields and load as `finished` — there is a test for exactly this
  (`test_a_record_written_before_the_job_model_still_loads`).
- On startup, any entry still marked `queued` or `running` is marked `failed`
  with "the server restarted before this download finished". That is deliberate:
  the queue lives in memory and the fetchers are subprocesses, so nothing
  survives a restart.

Rollback is redeploying the previous revision.

### 1.3 Turn on the API key

Enforcement is **off** while `API_KEY` is unset, so deploying does not enable it.
The switch is the env var.

**Order matters — step 2 before step 3, or the Android app starts failing
session-backed fetches the moment the server restarts.** The full sequence is in
`SSH.md` under "Turning on the API key". In short: generate a key, paste it into
the *current* Android app first, then add `API_KEY=…` to
`/opt/ttl-downloader/.env`, restart, then paste it into the web UI's Sessions →
Server key.

Rollback is removing `API_KEY` and restarting. No code change.

---

## 2. Blocked until Still Here mobile ships

Both are shims that exist only because `com.ttldownloader.app` (versionCode 5)
is in production. Both are recorded in `SSH.md` with the same trigger, and in
the Android repo's `docs/release-notes-1.0.md`.

### 2.1 Delete the synchronous download door

`POST /downloads` and `POST /instagram/downloads` without `?async=1` submit a job
and hold the connection open until it finishes. Still Here mobile 1.0 submits and
polls instead.

To remove: drop the `background` query parameter and the `_synchronous_response`
branch from `app/api/downloads.py` and `app/instagram/api/downloads.py`. The
`DownloadJobResponse` path becomes the only one.

Keep `tests/test_download_api.py::SynchronousDoorContractTests` until the day it
is deleted — every field in Android's `DownloadResponse` has a default, so a
shape regression shows as *zero files*, not an error.

### 2.2 Delete the `/tiktok` nginx prefix

Still Here mobile 1.0 defaults to `/stillhere`. Once old installs are drained,
delete both `/tiktok` blocks and reload. Nothing else references the prefix.

---

## 3. Accepted risks — decisions, not oversights

Recorded here so a later reader does not "fix" them by surprise. Each was
offered and declined during the auth brainstorm; each is independently additive
if it ever becomes uncomfortable.

| Risk | Why it is open |
|---|---|
| A stranger with the link can **delete** saved recordings and downloads | Every `DELETE` route stays open. Locking them was offered and declined. |
| A stranger can **fill the disk** with public, cookie-less downloads | No rate limit, no disk quota. Note the VPS has prior form: a full disk took the box down (`bad-core` crash loop, 2026-08-10). |
| A stranger can **see what has been saved** and fetch the media | The register listing and all file URLs stay open — they are plain navigations that cannot carry a header. |

Volume is 19 GB with roughly 6 GB used.

---

## 4. Two deviations from the API-key spec

Both are commented at the route so they do not read as bugs.

### 4.1 `POST /watch-recordings` is not gated

The spec lists it in Tier 2. A watch is a background loop that outlives its
request by hours, so carrying an authorisation decision that far is a different
change from threading a parameter. The recording a watch eventually starts still
passes the gated `can_record` check.

**Revisit if** watches ever become expensive enough to be worth abusing.

### 4.2 `POST /recordings` is gated at the live check, not at the recorder

The vendor recorder reads a fixed `vendor/tiktok-live-recorder/src/cookies.json`
path that cannot be varied per request. So an anonymous caller resolves the room
cookie-less, a restricted live comes back not-recordable, and the recorder never
starts. The session is not spendable — but the mechanism is indirect rather than
enforced at the subprocess.

**Properly fixing this** means teaching the vendor recorder to take a cookie path
argument, which is a vendor patch.

---

## 5. Housekeeping

- **`app/templates/_session_scripts.html` is dead.** Nothing includes it; it is
  a leftover from before `session-panel.js` existed, and it still contains the
  pre-redesign `sessionPill` markup. Delete it.
- **Ship the branch.** 18 commits, unpushed, no upstream set.
- **Out of scope by decision**, from the two specs: batch multi-URL paste,
  download prioritisation, retry-on-failure, per-download progress percentages,
  rate limiting, disk quotas, gating deletes, multi-user accounts, key rotation,
  per-client keys.
