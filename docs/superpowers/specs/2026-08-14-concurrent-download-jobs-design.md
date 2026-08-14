# Concurrent downloads as background jobs — design

> **Status:** designed, not implemented. Written 2026-08-14 to be executed in a
> later session. Generate the task-by-task plan from this with
> `superpowers:writing-plans` before touching code.

**Goal:** let several posts download at once, each visible with its own live status,
without breaking the Android client that is currently in use.

## Why downloads are "one at a time" today

They are not — on the server. This was investigated before designing, and the belief
turns out to be a UI limitation that got described as a backend one.

**Nothing serializes downloads.** There is no lock, semaphore, or single-flight guard
in `app/api/downloads.py`, `app/services/post_download_service.py`, or
`app/instagram/services/instagram_download_service.py`. The codebase plainly knows how
to serialize when it wants to: `app/api/live_relay.py` uses
`threading.BoundedSemaphore(max_concurrent_live_relays)` and `RecorderService` holds an
`RLock`. Downloads have neither.

The handlers are all sync `def`, which FastAPI runs in the AnyIO threadpool, so two
simultaneous POSTs already execute in parallel. The supporting machinery is
concurrency-safe: both cookie writers use `tempfile.mkstemp` (unique per call, no
shared-file collision), `DownloadStore` is `RLock`-guarded, and each download writes to
its own `download_id` directory.

The constraint lives in `app/static/js/save-page.js`: `submitButton.disabled = true`
while a request is in flight, and a single result slot where each new download's card
replaces the last. The pre-redesign page copy said *"Downloads run one at a time"*,
describing the UI but reading as a statement about the backend.

## The constraint nobody has hit yet

`/stillhere/` and `/tiktok/` have **no `proxy_read_timeout`**, so nginx's 60-second
default applies. **Any download over 60 seconds already fails with a 504.** The UI
anticipates long fetches — the elapsed counter says "taking a bit longer than usual" at
30s, and the pre-redesign Instagram copy warned that carousels and stories may take
longer — so this is being hit already, and read as a download failure.

`/breaking-bad/` on the same box sets `proxy_read_timeout 86400`, so the fix pattern
exists locally.

This matters for concurrency: the VPS has 2 cores, so simultaneous fetches each run
slower and push *more* of them past the cliff. Concurrency without addressing the
timeout would make the symptom worse.

## Decisions

Taken during brainstorming, with the reasoning that produced them:

1. **Downloads become background jobs**, like recordings — not a longer synchronous
   request. This removes the timeout problem at its root rather than raising a number.
2. **Two run at once; the rest queue.** One per core on a 1.9 GB box, chosen over 3
   because `ffmpeg` may sit behind a fetch and this VPS has prior form for
   resource exhaustion (`bad-core` crash loop, 2026-08-10).
3. **Submissions stack; no batch paste.** The form clears on submit so the next link
   can be pasted immediately. Multi-URL paste is deliberately out of scope.
4. **The synchronous API stays as a transitional shim**, because the current Android
   app runs against production while Still Here mobile is being built.

## Job model

`DownloadEntry` grows the lifecycle fields `RecordingJob` already carries: `status`
(`queued` → `running` → `finished` | `failed`), the source `url`, `error`,
`started_at`, `finished_at`.

One model and one store, mirroring the recorder. `RecordingJob` already carries both
job lifecycle *and* the completed artifact plus `fetched_at`, so extending
`DownloadEntry` makes downloads match an established pattern instead of introducing a
second one. It also means the web UI can render download cards with the same
`.job-card` and `.stamp` markup recordings already use.

Note `DownloadEntry`'s docstring currently says "one completed download" and its
`fetched_at` field drives retention. Both need updating — see
[Edge cases](#edge-cases).

## Worker pool

A new `DownloadJobService` mirrors `RecorderService`:

- `submit(url, platform) -> DownloadEntry` — validates the URL, persists a `queued`
  entry, returns immediately.
- Two worker threads consume a `queue.Queue`. Work beyond two waits as `Queued`.
- The limit is `max_concurrent_downloads: int = 2` in `app/services/config.py`, beside
  the existing `max_concurrent_live_relays`, so it stays tunable by env var.

**The fetch logic does not change.** `PostDownloadService` and
`InstagramDownloadService` are called exactly as they are today, just from a worker
thread rather than the request thread. Keeping this boundary intact is what makes the
change small; resist the temptation to refactor the fetchers at the same time.

## API surface

| Endpoint | Behaviour |
|---|---|
| `POST /downloads` | **Unchanged.** Submits a job, waits for it, returns today's payload. |
| `POST /downloads?async=1` | Returns `{id, status: "queued"}` immediately. |
| `GET /downloads` | Now lists in-flight entries too, so the UI can poll. |
| `POST /instagram/downloads` | Mirrors the above exactly. |

The synchronous door exists **only** so the current Android app keeps working. It is a
temporary shim with an explicit deletion trigger, in the same spirit as the `/tiktok`
URL shim: **delete the synchronous path once Still Here mobile ships against the async
API.** Record that trigger in `SSH.md` next to the `/tiktok` retirement note.

`proxy_read_timeout` must be raised on the `/stillhere/` and `/tiktok/` nginx blocks
regardless — the sync door holds a connection open for the whole download, and today
that already 504s at 60 seconds.

## Web UI

- The form clears on submit; the button no longer disables. Paste, save, paste again.
- Downloads render as `.job-card`s in the Filed list, reusing the recordings markup, so
  `Queued` / `Working` / `Filed` / `Failed` stamps come free from the existing CSS.
- Polling follows `record-page.js`, including its tolerance for dropped requests — a
  single failed poll must not replace a real message with "Failed to fetch".
- `save-page.js` currently renders exactly one result into `#post-download-result`. It
  becomes a list renderer. Element ids stay as they are.

## Edge cases

- **Restart with jobs in flight.** `running` entries are orphaned — their subprocess
  died with the process. Mark them `failed` on startup rather than leaving them
  spinning forever in the UI.
- **Cleanup.** `CleanupService` keys off `fetched_at` and `output_dir`. Queued and
  failed entries have neither. It must skip them, not crash and not delete live work.
- **Instagram's fetch-once rule.** The Instagram endpoint deletes each file after it is
  served. The job model does not change that, but the UI must not present a consumed
  link as though it were still live.
- **Failures are per-job.** One bad URL fails its own card; the queue keeps running.
- **Duplicate submissions.** Submitting the same URL twice creates two jobs. That is
  acceptable — deduplication is out of scope and would surprise more than it helps.

## Testing

- `submit` returns before the work completes.
- A third submission sits `Queued` while two run, and starts when a slot frees.
- A failing job does not stall the queue.
- Orphaned `running` entries become `failed` on startup.
- `CleanupService` tolerates entries with no files and no `fetched_at`.
- The synchronous door still returns the exact payload the Android `DownloadResponse`
  parses — `status`, `download_id`, `output_dir`, `files`, `file_urls`.

That last one is the important one. Every field in Android's `DownloadResponse` has a
default, so a shape regression would not raise an error there — it would silently show
zero files. Assert the payload explicitly rather than trusting a 200.

## Rollout

1. Raise `proxy_read_timeout` on the `/stillhere/` and `/tiktok/` nginx blocks. This is
   independently useful and can ship first.
2. Deploy the job model with both doors open.
3. Verify the current Android app still downloads normally.
4. Switch the web UI to the async door.

Rollback is redeploying the previous revision; no data migration is involved, since
existing `DownloadEntry` records simply lack the new fields and should default to
`finished`.

## Out of scope

Separate specs, deliberately not folded in here:

- **API key session gating** — `docs/superpowers/specs/2026-08-14-api-key-session-gating-design.md`.
- **Still Here mobile** — the Android redesign. Its arrival is the trigger for deleting
  the synchronous door.
- **Batch multi-URL paste**, download prioritisation, retry-on-failure, and progress
  percentages within a single download.
