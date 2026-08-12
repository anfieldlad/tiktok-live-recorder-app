# Media retention — event-driven cleanup with a grace period

Date: 2026-08-12
Status: approved, ready to implement

## Problem

Three failures, one root cause: nothing owns the question of when media may be
deleted.

1. **A timer destroyed a finished recording.** A 3000-second recording of a live
   completed at 14:51; `CLEANUP_MAX_AGE_HOURS=3` made it eligible at 17:51; the
   18:01 sweep removed it before its owner downloaded it. Fixed defensively in
   `98b6dbe` (the sweep now skips files a job references), but the underlying
   model — delete by age — was never right.
2. **TikTok post downloads are never deleted.** Recordings and Instagram
   downloads delete themselves when served; `PostDownloadService` has no
   deletion logic at all. `output/posts/` accumulated folders from June until a
   sweep removed them.
3. **Serving deletes immediately, so an interrupted save loses the file.** The
   Android client streams a file straight into the gallery. If the connection
   drops halfway, the server has already scheduled the delete and the only
   recovery is to download the media from TikTok again.

Underneath all three: download ids live in an in-memory `_results` dict, so a
restart makes every completed download unreachable *and* uncleanable — files
nothing can serve and nothing will remove.

## Principle

**A timer may only delete something the user has already been given.**
Everything else goes when the user says so, or stays.

## Decisions

| Question | Decision |
|---|---|
| Do downloads survive a restart? | Yes — persist an index, like `jobs.json` |
| Does serving still delete? | No — serving stamps `fetched_at`; deletion follows 24h later |
| What about media never fetched? | Never auto-deleted; disk pressure is surfaced, not acted on |

## Design

### State

A new `data/downloads.json`, written through a `DownloadStore` that mirrors
`JobStore`: atomic replace via a temp file, and corrupt-file recovery that backs
the bad copy aside rather than losing the index. Entries carry `id`, `platform`
(`tiktok_post` | `instagram`), `output_dir`, `files`, `created_at`, and
`fetched_at` (null until served).

`PostDownloadService` and `InstagramDownloadService` drop their in-memory
`_results` dicts in favour of the store. Recordings keep using `jobs.json` and
gain the same `fetched_at` field.

### Deletion paths

There are exactly three, and no others:

| Trigger | Removes |
|---|---|
| `fetched_at` older than the grace period | The entry and its files |
| Explicit delete from the UI or API | The entry and its files, immediately |
| Orphan on disk older than the same window | Files no index entry references |

### Retention parameters

Every window is a named parameter, settable from the environment, with no
duration hardcoded anywhere in the sweep:

| Setting | Default | Governs |
|---|---|---|
| `RETENTION_FETCHED_HOURS` | 24 | How long a fetched item lingers before removal |
| `RETENTION_ORPHAN_HOURS` | 24 | How long an unreferenced file survives |
| `LOG_MAX_AGE_HOURS` | 72 | Recorder logs for jobs that no longer exist |
| `CLEANUP_INTERVAL_MINUTES` | 30 | How often the sweep runs |
| `STORAGE_SOFT_LIMIT_GB` | 20 | Threshold the UI and health endpoint warn past |

They are gathered into a `RetentionPolicy` dataclass built once from `Settings`
and handed to `CleanupService`, so a test can construct a policy with
second-scale windows instead of manipulating file mtimes, and changing a
duration never means touching sweep logic.

`CLEANUP_MAX_AGE_HOURS` stays supported as the fallback for both retention
windows when they are not set individually, so an existing `.env` keeps working
and the deployed `CLEANUP_MAX_AGE_HOURS=24` needs no edit.

An item with `fetched_at: null` is never swept regardless of any of these
values. That is a rule, not a duration, and it is not configurable.

An item with `fetched_at: null` is never swept, whatever its age. That is the
rule that would have saved the lost recording.

Expiry removes the record as well as the bytes: a swept recording disappears
from `jobs.json` and from the recordings list, exactly as it does today when
downloading deletes it. What changes is only *when* — a day after the download
rather than during it. A swept download entry likewise leaves
`data/downloads.json`.

### Serving

`GET /recordings/{id}/download`, `GET /downloads/{id}/files/{i}`,
`GET /instagram/downloads/{id}/files/{i}` and the Instagram zip route replace
their delete-on-serve background tasks with a stamp of `fetched_at`.

Starlette runs background tasks after the response completes, including when the
client disconnected mid-stream, so a partial save still stamps. That is
acceptable: the 24-hour window — not the accuracy of the stamp — is what makes a
retry possible. Re-fetching refreshes `fetched_at` and so extends the window.

### Disk pressure

`/health/details` gains `storage`: bytes used under `output/`, free space, and
a soft limit from a new `STORAGE_SOFT_LIMIT_GB` (default 20, matching what the
footer has always claimed). The footer's hardcoded "Storage limit: ~20 GB"
becomes the real figure and warns past a threshold. Nothing is deleted on
pressure — the user decides.

### Components

Following the existing small-service layout in `app/services/`:

| Component | Purpose |
|---|---|
| `download_store.py` (new) | Persist download entries; mirrors `JobStore` |
| `cleanup_service.py` (rewrite) | Sweep fetched-and-expired items, and orphans |
| `storage_report.py` (new, small) | Usage/free/limit for the health endpoint |
| `post_download_service.py`, `instagram_download_service.py` | Use the store instead of `_results` |
| `api/downloads.py`, `instagram/api/downloads.py`, `api/recordings.py` | Stamp instead of delete; add `DELETE /downloads/{id}` and `DELETE /instagram/downloads/{id}`, which do not exist today |

### UI

Recordings and downloads stay listed for a day after saving, showing when they
will be removed, with a Delete button for reclaiming the space now. This is a
deliberate change: today a recording vanishes from the list the moment it is
downloaded.

## Error handling

- If the index cannot be read, the sweep treats everything as claimed and
  deletes nothing. Losing media is worse than keeping garbage.
- A corrupt index is backed up and reset, matching `JobStore` behaviour, so a
  bad write cannot make the app unusable.
- Deleting files is best-effort (`missing_ok`, `ignore_errors`); a file already
  gone is not an error.
- Serving a file whose entry exists but whose bytes are missing returns 404 with
  a clear message, as it does today.

## Testing

- An unfetched item survives a sweep at 48 hours old.
- A fetched item is removed once past the grace period, and not before.
- An orphan with no index entry is removed.
- The index round-trips a restart, and recovers from a corrupt file.
- Serving stamps `fetched_at` instead of deleting.
- The existing regression test from the lost recording stays unchanged.

## Out of scope

- Deleting on disk pressure. Reporting only, by decision.
- Per-user quotas. The app has no users; it has one owner.
- Re-downloading expired media automatically. The URL is still in the history;
  running it again is a click.
