# Concurrent Download Jobs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let several posts download at once, each visible with its own live status, without breaking the Android client that is currently in production.

**Architecture:** `DownloadEntry` grows the lifecycle fields `RecordingJob` already carries (`status`, `url`, `error`, `started_at`, `finished_at`). A new `DownloadJobService` mirrors `RecorderService`: `submit()` persists a `queued` entry and returns immediately; a fixed pool of worker threads drains a `queue.Queue` and calls the *unchanged* `PostDownloadService` / `InstagramDownloadService` fetchers. The existing synchronous `POST /downloads` becomes a thin wrapper that submits a job and blocks on it, so the current Android app is unaffected.

**Tech Stack:** FastAPI, Pydantic v2, `threading` + `queue` from the stdlib, Jinja2 templates, vanilla JS (no build step).

**Source spec:** `docs/superpowers/specs/2026-08-14-concurrent-download-jobs-design.md`

## Global Constraints

- **No breaking change to the synchronous API.** `POST /downloads` and `POST /instagram/downloads` without a query string must keep returning exactly `status`, `download_id`, `output_dir`, `files`, `file_urls` (plus `zip_url` for Instagram). Every field in Android's `DownloadResponse` has a default, so a shape regression fails silently — assert the payload explicitly, never just the 200.
- **The fetch logic does not change.** `PostDownloadService` and `InstagramDownloadService` keep their current behaviour; the only permitted change is an optional `download_id` parameter so a pre-allocated id can be threaded through. Do not refactor the fetchers.
- **Two downloads run at once**, the rest queue. The limit is `max_concurrent_downloads: int = 2` in `app/services/config.py`, beside `max_concurrent_live_relays`, so it stays tunable by env var (`MAX_CONCURRENT_DOWNLOADS`).
- **Element ids in the web UI stay as they are.** `#post-download-result`, `#post-download-form`, `#post-url`, `#post-download-notice`, `#download-post-button`, `#clear-post-download-form`.
- **Bump the `?v=` query string on every changed static asset**, or the browser serves the old file.
- **Existing entries default to `finished`.** `data/downloads.json` records written before this change lack the new fields; they must load without migration.
- Test runner is `.venv/bin/python -m unittest`. There is no pytest in this project.
- No deployment, no nginx change, and no change to production as part of this plan. The `proxy_read_timeout` fix is recorded in the spec's rollout and stays a separate manual step.

---

### Task 1: The job model

**Files:**
- Modify: `app/models/download.py`
- Test: `tests/test_download_store.py`

**Interfaces:**
- Produces: `DownloadStatus` (str enum: `queued`, `running`, `finished`, `failed`); `DownloadEntry` with new fields `status`, `url`, `error`, `started_at`, `finished_at` and `output_dir` defaulting to `""`; `new_download_id() -> str`; `display_path(path: Path) -> str`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_download_store.py`:

```python
class DownloadEntryLifecycleTests(unittest.TestCase):
    def test_a_record_written_before_the_job_model_still_loads(self) -> None:
        """Old rows have no status; they are completed downloads, not queued ones."""
        with tempfile.TemporaryDirectory() as temp_dir:
            downloads_file = Path(temp_dir) / "downloads.json"
            downloads_file.write_text(
                json.dumps([
                    {
                        "id": "20260812-101500-abc123",
                        "platform": "tiktok_post",
                        "output_dir": "/tmp/out",
                        "files": ["/tmp/out/video.mp4"],
                        "created_at": "2026-08-12T10:15:00+00:00",
                        "fetched_at": None,
                    }
                ]),
                encoding="utf-8",
            )

            entries = DownloadStore(downloads_file).list_entries()

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].status, DownloadStatus.finished)
            self.assertIsNone(entries[0].url)

    def test_a_queued_entry_needs_no_output_dir(self) -> None:
        entry = DownloadEntry(
            id="20260815-090000-aaaaaa",
            platform=DownloadPlatform.tiktok_post,
            status=DownloadStatus.queued,
            url="https://www.tiktok.com/@someone/video/123",
        )

        self.assertEqual(entry.output_dir, "")
        self.assertEqual(entry.files, [])

    def test_new_download_id_is_unique_within_the_same_second(self) -> None:
        self.assertNotEqual(new_download_id(), new_download_id())
```

Extend the imports at the top of that file to:

```python
import json
from app.models.download import (
    DownloadEntry,
    DownloadPlatform,
    DownloadStatus,
    new_download_id,
)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_download_store -v`
Expected: FAIL with `ImportError: cannot import name 'DownloadStatus'`

- [ ] **Step 3: Write the model**

Replace the body of `app/models/download.py` below the imports with:

```python
from __future__ import annotations

import secrets
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from app.models.recording import utc_now
from app.services.config import PROJECT_ROOT


class DownloadPlatform(str, Enum):
    tiktok_post = "tiktok_post"
    instagram = "instagram"


class DownloadStatus(str, Enum):
    queued = "queued"
    running = "running"
    finished = "finished"
    failed = "failed"


def new_download_id() -> str:
    """A sortable, collision-resistant id, allocated before the fetch starts.

    Both fetchers used to mint this themselves at the moment they began. A job
    needs its id at submit time so the caller has something to poll.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{secrets.token_hex(3)}"


def display_path(path: Path) -> str:
    """Project-relative where possible, so the register does not publish the
    server's filesystem layout."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


class DownloadEntry(BaseModel):
    """One download, from the moment it is queued to the moment it is swept.

    This used to describe only *completed* work. It now carries the same
    lifecycle fields RecordingJob does, so queued and running downloads are
    visible to the UI instead of existing only inside a request that has not
    returned yet.

    `fetched_at` remains the retention key: without it, a restart left files on
    disk that nothing could serve and nothing would remove. A queued or failed
    entry has no files and no `fetched_at`, and the sweep must skip it.

    Every new field has a default, so rows written before the job model load
    unchanged and read as `finished`.
    """

    id: str
    platform: DownloadPlatform
    status: DownloadStatus = DownloadStatus.finished
    url: Optional[str] = None
    output_dir: str = ""
    files: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None

    def is_terminal(self) -> bool:
        return self.status in {DownloadStatus.finished, DownloadStatus.failed}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_download_store -v`
Expected: PASS, all tests in the file

- [ ] **Step 5: Run the whole suite — nothing else may break**

Run: `.venv/bin/python -m unittest discover -s tests`
Expected: `OK` — 57 existing tests plus the 3 new ones

- [ ] **Step 6: Commit**

```bash
git add app/models/download.py tests/test_download_store.py
git commit -m "feat: give a download entry the lifecycle a job has"
```

---

### Task 2: An updatable store, and reconciling orphans on startup

**Files:**
- Modify: `app/services/download_store.py`
- Test: `tests/test_download_store.py`

**Interfaces:**
- Consumes: `DownloadStatus`, `DownloadEntry` from Task 1.
- Produces: `DownloadStore.update_entry(download_id: str, updater: Callable[[DownloadEntry], DownloadEntry]) -> DownloadEntry | None`; `DownloadStore.fail_orphaned_jobs() -> int`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_download_store.py`:

```python
class DownloadStoreJobTests(unittest.TestCase):
    def store(self, temp_dir: str) -> DownloadStore:
        return DownloadStore(Path(temp_dir) / "downloads.json")

    def test_update_entry_applies_the_updater(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            store.save_entry(
                DownloadEntry(id="a", platform=DownloadPlatform.tiktok_post, status=DownloadStatus.queued)
            )

            updated = store.update_entry(
                "a", lambda current: current.model_copy(update={"status": DownloadStatus.running})
            )

            self.assertIsNotNone(updated)
            self.assertEqual(store.get_entry("a").status, DownloadStatus.running)

    def test_update_entry_on_an_unknown_id_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            self.assertIsNone(store.update_entry("nope", lambda current: current))

    def test_a_restart_fails_jobs_that_were_still_in_flight(self) -> None:
        """Their subprocess died with the process. Left alone they spin forever."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            store.save_entry(DownloadEntry(id="q", platform=DownloadPlatform.tiktok_post, status=DownloadStatus.queued))
            store.save_entry(DownloadEntry(id="r", platform=DownloadPlatform.instagram, status=DownloadStatus.running))
            store.save_entry(
                DownloadEntry(id="f", platform=DownloadPlatform.tiktok_post, status=DownloadStatus.finished)
            )

            failed = store.fail_orphaned_jobs()

            self.assertEqual(failed, 2)
            self.assertEqual(store.get_entry("q").status, DownloadStatus.failed)
            self.assertEqual(store.get_entry("r").status, DownloadStatus.failed)
            self.assertEqual(store.get_entry("f").status, DownloadStatus.finished)
            self.assertIn("restart", store.get_entry("r").error)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_download_store -v`
Expected: FAIL with `AttributeError: 'DownloadStore' object has no attribute 'update_entry'`

- [ ] **Step 3: Add the methods**

In `app/services/download_store.py`, add `Callable` to the imports:

```python
from typing import Callable
```

and add these two methods after `save_entry`:

```python
    def update_entry(
        self, download_id: str, updater: Callable[[DownloadEntry], DownloadEntry]
    ) -> DownloadEntry | None:
        """Read-modify-write under the lock. Mirrors JobStore.update_job.

        Two workers and a request thread all write this file, so callers must
        never read, mutate and save as three separate steps.
        """
        with self._lock:
            entries = self._read()
            for index, entry in enumerate(entries):
                if entry.id == download_id:
                    entries[index] = updater(entry)
                    self._write(entries)
                    return entries[index]
        return None

    def fail_orphaned_jobs(self) -> int:
        """Mark work that was in flight when the process died.

        The queue lives in memory and the fetchers are subprocesses, so nothing
        survives a restart. Without this, a queued or running entry spins in the
        UI forever waiting for a worker that no longer exists.
        """
        with self._lock:
            entries = self._read()
            changed = 0
            for index, entry in enumerate(entries):
                if entry.status not in {DownloadStatus.queued, DownloadStatus.running}:
                    continue
                entries[index] = entry.model_copy(
                    update={
                        "status": DownloadStatus.failed,
                        "error": "the server restarted before this download finished",
                        "finished_at": utc_now(),
                    }
                )
                changed += 1
            if changed:
                self._write(entries)
        return changed
```

Extend the module import to `from app.models.download import DownloadEntry, DownloadStatus`.

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_download_store -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/services/download_store.py tests/test_download_store.py
git commit -m "feat: let the download store update an entry and reconcile orphans"
```

---

### Task 3: A pre-allocated id, threaded through the fetchers

**Files:**
- Modify: `app/services/post_download_service.py`
- Modify: `app/instagram/services/instagram_download_service.py`
- Test: `tests/test_download_services.py`

**Interfaces:**
- Consumes: `new_download_id`, `DownloadStatus` from Task 1; `DownloadStore.update_entry` from Task 2.
- Produces: `PostDownloadService.download(url: str, download_id: str | None = None) -> PostDownloadResult`; `InstagramDownloadService.download(url: str, download_id: str | None = None) -> InstagramDownloadResult`. Both services' `remember()` now upserts: it merges into an existing entry rather than overwriting it with a fresh `finished` one.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_download_services.py`:

```python
class DownloadIdOwnershipTests(unittest.TestCase):
    """A job needs its id before the fetch begins, so the caller can poll it."""

    def test_remember_keeps_the_lifecycle_the_job_service_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "data" / "downloads.json")
            service = PostDownloadService(root / "output", download_store=store)
            store.save_entry(
                DownloadEntry(
                    id="20260815-101500-abc123",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.running,
                    url="https://www.tiktok.com/@someone/video/123",
                )
            )

            download_dir = service.output_dir / "20260815-101500-abc123"
            download_dir.mkdir(parents=True)
            media = download_dir / "video.mp4"
            media.write_bytes(b"x" * 10)
            service.remember(
                PostDownloadResult(
                    download_id="20260815-101500-abc123", output_dir=download_dir, files=[media]
                )
            )

            entry = store.get_entry("20260815-101500-abc123")
            self.assertEqual(entry.status, DownloadStatus.running, "the worker owns the status, not remember()")
            self.assertEqual(entry.url, "https://www.tiktok.com/@someone/video/123")
            self.assertEqual(entry.files, [str(media)])

    def test_remember_still_creates_an_entry_when_nothing_pre_allocated_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "data" / "downloads.json")
            service = PostDownloadService(root / "output", download_store=store)

            download_dir = service.output_dir / "20260815-101500-def456"
            download_dir.mkdir(parents=True)
            media = download_dir / "video.mp4"
            media.write_bytes(b"x" * 10)
            service.remember(
                PostDownloadResult(
                    download_id="20260815-101500-def456", output_dir=download_dir, files=[media]
                )
            )

            self.assertEqual(store.get_entry("20260815-101500-def456").status, DownloadStatus.finished)
```

Add to that file's imports:

```python
from app.models.download import DownloadEntry, DownloadPlatform, DownloadStatus
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_download_services -v`
Expected: FAIL — `remember()` overwrites the entry, so `status` reads `finished` and `url` reads `None`

- [ ] **Step 3: Make `remember` an upsert and accept a caller's id**

In `app/services/post_download_service.py`:

Replace the import `from app.models.download import DownloadEntry, DownloadPlatform` with:

```python
from app.models.download import DownloadEntry, DownloadPlatform, new_download_id
```

Replace `remember`:

```python
    def remember(self, result: PostDownloadResult) -> PostDownloadResult:
        """Record where a download's files landed.

        This merges rather than replaces: when a job service pre-allocated the
        entry, the lifecycle fields on it (status, url, started_at) belong to
        that service and must survive. Only the synchronous door reaches the
        fallback branch, and there a completed download is exactly what this is.
        """
        if self.download_store is None:
            self._results[result.download_id] = result
            return result

        updated = self.download_store.update_entry(
            result.download_id,
            lambda current: current.model_copy(
                update={
                    "output_dir": str(result.output_dir),
                    "files": [str(path) for path in result.files],
                }
            ),
        )
        if updated is None:
            self.download_store.save_entry(
                DownloadEntry(
                    id=result.download_id,
                    platform=DownloadPlatform.tiktok_post,
                    output_dir=str(result.output_dir),
                    files=[str(path) for path in result.files],
                )
            )
        return result
```

Change the `download` signature and its first lines:

```python
    def download(self, url: str, download_id: str | None = None) -> PostDownloadResult:
        normalized_url = self.validate_url(url)
        download_id = download_id or new_download_id()
        download_dir = self.output_dir / download_id
        download_dir.mkdir(parents=True, exist_ok=False)
```

Delete the now-unused `_new_download_id` method and the `secrets` / `datetime` imports if nothing else in the file uses them (`datetime` is not used elsewhere; `secrets` is not either).

Apply the identical three changes to `app/instagram/services/instagram_download_service.py`, with `DownloadPlatform.instagram` in the fallback branch. That file keeps `secrets` (used by `create_archive`) but loses `_new_download_id`; leave the `datetime` import only if still referenced (it is not — remove it).

- [ ] **Step 4: Run the test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_download_services -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m unittest discover -s tests`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/services/post_download_service.py app/instagram/services/instagram_download_service.py tests/test_download_services.py
git commit -m "feat: let a caller pre-allocate a download id"
```

---

### Task 4: The worker pool

**Files:**
- Create: `app/services/download_job_service.py`
- Modify: `app/services/config.py`
- Test: `tests/test_download_job_service.py`

**Interfaces:**
- Consumes: `DownloadEntry`, `DownloadStatus`, `DownloadPlatform`, `new_download_id` (Task 1); `DownloadStore.update_entry` (Task 2); `download(url, download_id=...)` (Task 3).
- Produces:
  - `DownloadJobService(download_store, post_download_service, instagram_download_service, max_workers: int = 2, start: bool = True)`
  - `.submit(url: str, platform: DownloadPlatform) -> DownloadEntry` — validates, persists `queued`, returns immediately. Raises `ValueError` for a URL the platform's validator rejects.
  - `.wait(download_id: str, timeout: float | None = None) -> DownloadEntry | None` — blocks until terminal.
  - `.diagnostics() -> dict[str, object]`
  - `settings.max_concurrent_downloads: int = 2`

- [ ] **Step 1: Write the failing test**

Create `tests/test_download_job_service.py`:

```python
from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from app.models.download import DownloadPlatform, DownloadStatus
from app.services.download_job_service import DownloadJobService
from app.services.download_store import DownloadStore


class FakeDownloadService:
    """Stands in for a fetcher. Each call blocks until the test releases it."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.started = threading.Semaphore(0)
        self.release = threading.Event()
        self.failing_urls: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def validate_url(self, url: str) -> str:
        if "bad" in url:
            raise ValueError("download URL must be a TikTok URL")
        return url.strip()

    def download(self, url: str, download_id: str | None = None):
        with self._lock:
            self.calls.append((url, download_id or ""))
        self.started.release()
        self.release.wait(timeout=5)
        if url in self.failing_urls:
            raise RuntimeError("that post is no longer available")
        directory = self.output_dir / (download_id or "x")
        directory.mkdir(parents=True, exist_ok=True)
        return None


class DownloadJobServiceTests(unittest.TestCase):
    def build(self, temp_dir: str, max_workers: int = 2):
        root = Path(temp_dir)
        store = DownloadStore(root / "data" / "downloads.json")
        tiktok = FakeDownloadService(root / "output" / "posts")
        instagram = FakeDownloadService(root / "output" / "instagram")
        service = DownloadJobService(store, tiktok, instagram, max_workers=max_workers)
        self.addCleanup(service.stop)
        return store, tiktok, instagram, service

    def test_submit_returns_before_the_work_completes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, tiktok, _, service = self.build(temp_dir)

            entry = service.submit("https://www.tiktok.com/@a/video/1", DownloadPlatform.tiktok_post)

            self.assertIn(entry.status, {DownloadStatus.queued, DownloadStatus.running})
            self.assertEqual(store.get_entry(entry.id).url, "https://www.tiktok.com/@a/video/1")
            self.assertTrue(tiktok.started.acquire(timeout=5), "a worker should have picked the job up")
            tiktok.release.set()

    def test_a_rejected_url_never_becomes_a_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, _, _, service = self.build(temp_dir)

            with self.assertRaises(ValueError):
                service.submit("https://example.com/bad", DownloadPlatform.tiktok_post)

            self.assertEqual(store.list_entries(), [])

    def test_a_third_submission_waits_for_a_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, tiktok, _, service = self.build(temp_dir, max_workers=2)

            first = service.submit("https://www.tiktok.com/@a/video/1", DownloadPlatform.tiktok_post)
            second = service.submit("https://www.tiktok.com/@a/video/2", DownloadPlatform.tiktok_post)
            third = service.submit("https://www.tiktok.com/@a/video/3", DownloadPlatform.tiktok_post)

            self.assertTrue(tiktok.started.acquire(timeout=5))
            self.assertTrue(tiktok.started.acquire(timeout=5))
            self.assertEqual(store.get_entry(third.id).status, DownloadStatus.queued)
            self.assertEqual(len(tiktok.calls), 2, "only two may run at once")

            tiktok.release.set()
            self.assertIsNotNone(service.wait(first.id, timeout=5))
            self.assertIsNotNone(service.wait(second.id, timeout=5))
            self.assertEqual(service.wait(third.id, timeout=5).status, DownloadStatus.finished)

    def test_a_failing_job_does_not_stall_the_queue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store, tiktok, _, service = self.build(temp_dir, max_workers=1)
            tiktok.failing_urls.add("https://www.tiktok.com/@a/video/dead")
            tiktok.release.set()

            bad = service.submit("https://www.tiktok.com/@a/video/dead", DownloadPlatform.tiktok_post)
            good = service.submit("https://www.tiktok.com/@a/video/2", DownloadPlatform.tiktok_post)

            failed = service.wait(bad.id, timeout=5)
            self.assertEqual(failed.status, DownloadStatus.failed)
            self.assertIn("no longer available", failed.error)
            self.assertEqual(service.wait(good.id, timeout=5).status, DownloadStatus.finished)

    def test_the_worker_gets_the_id_that_was_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, tiktok, _, service = self.build(temp_dir, max_workers=1)
            tiktok.release.set()

            entry = service.submit("https://www.tiktok.com/@a/video/1", DownloadPlatform.tiktok_post)
            service.wait(entry.id, timeout=5)

            self.assertEqual(tiktok.calls[0][1], entry.id)

    def test_instagram_jobs_go_to_the_instagram_fetcher(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            _, tiktok, instagram, service = self.build(temp_dir, max_workers=1)
            instagram.release.set()

            entry = service.submit("https://www.instagram.com/p/abc/", DownloadPlatform.instagram)
            service.wait(entry.id, timeout=5)

            self.assertEqual(len(instagram.calls), 1)
            self.assertEqual(tiktok.calls, [])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_download_job_service -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.download_job_service'`

- [ ] **Step 3: Write the service**

Create `app/services/download_job_service.py`:

```python
"""Downloads as background jobs, mirroring RecorderService.

Nothing here serializes work that used to run in parallel — the handlers were
always sync `def` and always ran concurrently in the threadpool. What this adds
is a *bound* on that concurrency (a 1.9 GB, 2-core box will not survive an
unbounded number of yt-dlp processes) and a persisted lifecycle, so a download
in progress is something the UI can see rather than something hidden inside a
request that has not returned yet.

The fetchers are untouched. They are called from a worker thread with an id the
caller already knows, and that is the whole of the change on their side.
"""

from __future__ import annotations

import logging
import queue
import threading

from app.models.download import (
    DownloadEntry,
    DownloadPlatform,
    DownloadStatus,
    new_download_id,
)
from app.models.recording import utc_now
from app.services.download_store import DownloadStore
from app.services.redaction import redact_sensitive


logger = logging.getLogger(__name__)

_STOP = "__stop__"


class DownloadJobService:
    def __init__(
        self,
        download_store: DownloadStore,
        post_download_service: object,
        instagram_download_service: object,
        max_workers: int = 2,
        start: bool = True,
    ) -> None:
        self.download_store = download_store
        self._services = {
            DownloadPlatform.tiktok_post: post_download_service,
            DownloadPlatform.instagram: instagram_download_service,
        }
        self._max_workers = max(1, int(max_workers))
        self._queue: queue.Queue[str] = queue.Queue()
        self._finished: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._running: set[str] = set()
        self._threads: list[threading.Thread] = []
        if start:
            self.start()

    def start(self) -> None:
        for index in range(self._max_workers):
            thread = threading.Thread(
                target=self._worker_loop, name=f"download-worker-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self) -> None:
        """Only used by tests; the daemon threads die with the process."""
        for _ in self._threads:
            self._queue.put(_STOP)

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            running = sorted(self._running)
        return {
            "max_workers": self._max_workers,
            "running_count": len(running),
            "running": running,
            "queued_count": self._queue.qsize(),
        }

    def submit(self, url: str, platform: DownloadPlatform) -> DownloadEntry:
        """Persist a queued job and return at once.

        Validation happens here, on the request thread, so a link that is not a
        TikTok or Instagram URL is still a 422 and never becomes a job.
        """
        service = self._services[platform]
        normalized_url = service.validate_url(url)

        entry = DownloadEntry(
            id=new_download_id(),
            platform=platform,
            status=DownloadStatus.queued,
            url=normalized_url,
        )
        self.download_store.save_entry(entry)
        with self._lock:
            self._finished[entry.id] = threading.Event()
        self._queue.put(entry.id)
        return entry

    def wait(self, download_id: str, timeout: float | None = None) -> DownloadEntry | None:
        """Block until the job reaches a terminal state.

        This is what the synchronous door is built on: the current Android app
        posts and waits for the payload, and must keep working exactly as it
        does today.
        """
        with self._lock:
            event = self._finished.get(download_id)
        if event is not None:
            event.wait(timeout)
        return self.download_store.get_entry(download_id)

    def _worker_loop(self) -> None:
        while True:
            download_id = self._queue.get()
            try:
                if download_id == _STOP:
                    return
                self._run(download_id)
            except Exception:  # a worker thread must never die
                logger.exception("Unhandled download job failure", extra={"download_id": download_id})
            finally:
                self._queue.task_done()
                if download_id != _STOP:
                    with self._lock:
                        event = self._finished.get(download_id)
                    if event is not None:
                        event.set()

    def _run(self, download_id: str) -> None:
        entry = self.download_store.get_entry(download_id)
        if entry is None:
            return

        self.download_store.update_entry(
            download_id,
            lambda current: current.model_copy(
                update={"status": DownloadStatus.running, "started_at": utc_now(), "error": None}
            ),
        )
        with self._lock:
            self._running.add(download_id)

        try:
            service = self._services[entry.platform]
            service.download(entry.url, download_id=download_id)
        except Exception as exc:
            # ValueError (a bad URL) cannot reach here — submit() validated it —
            # so anything caught is a fetch failure and belongs on the card.
            logger.info("Download job failed", extra={"download_id": download_id})
            self.download_store.update_entry(
                download_id,
                lambda current: current.model_copy(
                    update={
                        "status": DownloadStatus.failed,
                        "error": redact_sensitive(str(exc)) or "the download failed",
                        "finished_at": utc_now(),
                    }
                ),
            )
        else:
            self.download_store.update_entry(
                download_id,
                lambda current: current.model_copy(
                    update={
                        "status": DownloadStatus.finished,
                        "error": None,
                        "finished_at": utc_now(),
                    }
                ),
            )
        finally:
            with self._lock:
                self._running.discard(download_id)
```

- [ ] **Step 4: Add the setting**

In `app/services/config.py`, add below `max_concurrent_live_relays`:

```python
    # One per core on a 1.9 GB box. Chosen over 3 because ffmpeg may sit behind
    # a fetch, and this VPS has prior form for resource exhaustion.
    max_concurrent_downloads: int = 2
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_download_job_service -v`
Expected: PASS, 6 tests

- [ ] **Step 6: Commit**

```bash
git add app/services/download_job_service.py app/services/config.py tests/test_download_job_service.py
git commit -m "feat: run downloads as bounded background jobs"
```

---

### Task 5: The API surface — an async door and a list

**Files:**
- Modify: `app/models/download.py`
- Modify: `app/api/downloads.py`
- Modify: `app/instagram/api/downloads.py`
- Modify: `app/main.py`
- Test: `tests/test_download_api.py` (create)

**Interfaces:**
- Consumes: everything from Tasks 1–4.
- Produces:
  - `DownloadJobResponse` in `app/models/download.py`, with `.from_entry(entry: DownloadEntry) -> DownloadJobResponse`
  - `POST /downloads?async=1` → `DownloadJobResponse` with `status: "queued"`
  - `POST /downloads` (no query) → today's `PostDownloadCreateResponse`, unchanged
  - `GET /downloads` → `list[DownloadJobResponse]`, newest first, all platforms
  - `POST /instagram/downloads?async=1` → same as above
  - `app.state.download_job_service`

- [ ] **Step 1: Write the failing test**

Create `tests/test_download_api.py`:

```python
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.download import DownloadEntry, DownloadPlatform, DownloadStatus


class DownloadApiTests(unittest.TestCase):
    def create_test_client(self) -> TestClient:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        for key, value in {
            "JOBS_FILE": str(temp_root / "data" / "jobs.json"),
            "WATCH_JOBS_FILE": str(temp_root / "data" / "watch_jobs.json"),
            "DOWNLOADS_FILE": str(temp_root / "data" / "downloads.json"),
            "OUTPUT_DIR": str(temp_root / "output"),
            "LOGS_DIR": str(temp_root / "logs"),
            "RECORDER_DIR": str(temp_root / "vendor" / "recorder"),
            "RECORDER_ENTRYPOINT": str(temp_root / "vendor" / "recorder" / "src" / "main.py"),
            "RECORDER_COOKIES_FILE": str(temp_root / "data" / "cookies.json"),
            "INSTAGRAM_COOKIES_FILE": str(temp_root / "data" / "instagram_cookies.json"),
            "ROOT_PATH": "",
        }.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        (temp_root / "vendor" / "recorder" / "src").mkdir(parents=True, exist_ok=True)
        app = create_app()
        self.addCleanup(self.temp_dir.cleanup)
        self.app = app
        return TestClient(app)

    def test_the_async_door_returns_a_queued_job_immediately(self) -> None:
        client = self.create_test_client()

        response = client.post(
            "/downloads?async=1", json={"url": "https://www.tiktok.com/@someone/video/123"}
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertTrue(body["id"])
        self.assertEqual(body["platform"], "tiktok_post")
        self.assertEqual(body["files"], [])

    def test_a_bad_url_is_still_a_422_on_the_async_door(self) -> None:
        client = self.create_test_client()

        response = client.post("/downloads?async=1", json={"url": "https://example.com/x"})

        self.assertEqual(response.status_code, 422)

    def test_the_list_shows_in_flight_work(self) -> None:
        client = self.create_test_client()
        store = self.app.state.download_store
        store.save_entry(
            DownloadEntry(
                id="20260815-101500-aaaaaa",
                platform=DownloadPlatform.instagram,
                status=DownloadStatus.running,
                url="https://www.instagram.com/p/abc/",
            )
        )

        body = client.get("/downloads").json()

        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["status"], "running")
        self.assertEqual(body[0]["platform"], "instagram")
        self.assertEqual(body[0]["file_urls"], [])

    def test_a_finished_entry_carries_platform_correct_urls(self) -> None:
        client = self.create_test_client()
        store = self.app.state.download_store
        output_dir = Path(self.temp_dir.name) / "output" / "instagram" / "20260815-101500-bbbbbb"
        output_dir.mkdir(parents=True)
        media = output_dir / "reel.mp4"
        media.write_bytes(b"x")
        store.save_entry(
            DownloadEntry(
                id="20260815-101500-bbbbbb",
                platform=DownloadPlatform.instagram,
                status=DownloadStatus.finished,
                output_dir=str(output_dir),
                files=[str(media)],
            )
        )

        body = client.get("/downloads").json()

        self.assertEqual(
            body[0]["file_urls"], ["/instagram/downloads/20260815-101500-bbbbbb/files/0"]
        )
        self.assertEqual(body[0]["zip_url"], "/instagram/downloads/20260815-101500-bbbbbb/zip")

    def test_a_tiktok_entry_has_no_zip_url(self) -> None:
        client = self.create_test_client()
        store = self.app.state.download_store
        store.save_entry(
            DownloadEntry(
                id="20260815-101500-cccccc",
                platform=DownloadPlatform.tiktok_post,
                status=DownloadStatus.finished,
                output_dir="/tmp/x",
                files=["/tmp/x/video.mp4"],
            )
        )

        body = client.get("/downloads").json()

        self.assertIsNone(body[0]["zip_url"])
        self.assertEqual(body[0]["file_urls"], ["/downloads/20260815-101500-cccccc/files/0"])


class SynchronousDoorContractTests(DownloadApiTests):
    """Android's DownloadResponse defaults every field, so a shape regression
    shows as zero files rather than an error. Assert the payload, not the 200."""

    def test_the_synchronous_payload_keeps_every_field_android_reads(self) -> None:
        client = self.create_test_client()
        store = self.app.state.download_store
        service = self.app.state.post_download_service
        output_dir = service.output_dir / "20260815-101500-dddddd"
        output_dir.mkdir(parents=True)
        media = output_dir / "video.mp4"
        media.write_bytes(b"x")

        # Drive the real route, but with a fetcher that does nothing but write
        # the files a successful yt-dlp run would have left behind.
        def fake_download(url: str, download_id: str | None = None):
            from app.services.post_download_service import PostDownloadResult

            return service.remember(
                PostDownloadResult(
                    download_id=download_id, output_dir=output_dir, files=[media]
                )
            )

        original = service.download
        service.download = fake_download
        self.addCleanup(lambda: setattr(service, "download", original))

        response = client.post(
            "/downloads", json={"url": "https://www.tiktok.com/@someone/video/123"}
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(sorted(body.keys()), ["download_id", "file_urls", "files", "output_dir", "status"])
        self.assertEqual(body["status"], "finished")
        self.assertTrue(body["download_id"])
        self.assertEqual(len(body["files"]), 1)
        self.assertEqual(body["file_urls"], [f"/downloads/{body['download_id']}/files/0"])

    def test_a_failing_fetch_is_still_a_400_on_the_synchronous_door(self) -> None:
        client = self.create_test_client()
        service = self.app.state.post_download_service

        def fake_download(url: str, download_id: str | None = None):
            raise RuntimeError("This post is no longer available on TikTok.")

        original = service.download
        service.download = fake_download
        self.addCleanup(lambda: setattr(service, "download", original))

        response = client.post(
            "/downloads", json={"url": "https://www.tiktok.com/@someone/video/123"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no longer available", response.json()["detail"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_download_api -v`
Expected: FAIL — `/downloads?async=1` returns the synchronous payload and `GET /downloads` is a 405

- [ ] **Step 3: Add the response model**

Append to `app/models/download.py`:

```python
class DownloadJobResponse(BaseModel):
    """One row of the register, whatever state it is in.

    Deliberately separate from the synchronous PostDownloadCreateResponse: that
    payload is a contract with a shipped Android build and must not grow fields.
    """

    id: str
    platform: DownloadPlatform
    status: DownloadStatus
    url: Optional[str]
    error: Optional[str]
    output_dir: str
    files: list[str]
    file_urls: list[str]
    zip_url: Optional[str] = None
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]
    fetched_at: Optional[datetime]

    @classmethod
    def from_entry(cls, entry: DownloadEntry) -> "DownloadJobResponse":
        base = (
            "/instagram/downloads"
            if entry.platform == DownloadPlatform.instagram
            else "/downloads"
        )
        return cls(
            id=entry.id,
            platform=entry.platform,
            status=entry.status,
            url=entry.url,
            error=entry.error,
            output_dir=display_path(entry.output_dir) if entry.output_dir else "",
            files=[display_path(path) for path in entry.files],
            file_urls=[f"{base}/{entry.id}/files/{index}" for index, _ in enumerate(entry.files)],
            zip_url=(
                f"{base}/{entry.id}/zip"
                if entry.platform == DownloadPlatform.instagram and entry.files
                else None
            ),
            created_at=entry.created_at,
            started_at=entry.started_at,
            finished_at=entry.finished_at,
            fetched_at=entry.fetched_at,
        )
```

- [ ] **Step 4: Rewrite the two POST routes and add the list**

In `app/api/downloads.py`, replace the imports and `create_download`, and add `list_downloads`:

```python
from fastapi import APIRouter, HTTPException, Query, Request, status

from app.models.download import (
    DownloadEntry,
    DownloadJobResponse,
    DownloadPlatform,
    DownloadStatus,
    display_path,
)
```

```python
@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_download(
    request: Request,
    payload: PostDownloadCreateRequest,
    background: bool = Query(default=False, alias="async"),
) -> PostDownloadCreateResponse | DownloadJobResponse:
    """Two doors onto one queue.

    `?async=1` is the register's door: it returns a job id to poll. The bare
    POST is the shim the shipped Android app uses — it submits the same job and
    holds the connection until it finishes, returning the payload that build
    parses. Delete it once Still Here mobile ships against the async door.
    """
    job_service = request.app.state.download_job_service
    try:
        entry = job_service.submit(payload.url, DownloadPlatform.tiktok_post)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if background:
        return DownloadJobResponse.from_entry(entry)

    finished = job_service.wait(entry.id)
    return _synchronous_response(finished)


@router.get("", response_model=list[DownloadJobResponse])
def list_downloads(request: Request) -> list[DownloadJobResponse]:
    """Every entry, both platforms, newest first — the register's Filed list."""
    store = request.app.state.download_store
    return [DownloadJobResponse.from_entry(entry) for entry in store.list_entries()]
```

Add the helper below `_file_urls`:

```python
def _synchronous_response(entry: DownloadEntry | None) -> PostDownloadCreateResponse:
    """The exact payload the shipped Android build parses. Do not add fields."""
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="the download disappeared"
        )
    if entry.status != DownloadStatus.finished:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=entry.error or "the download failed"
        )
    return PostDownloadCreateResponse(
        status="finished",
        download_id=entry.id,
        output_dir=display_path(entry.output_dir),
        files=[display_path(path) for path in entry.files],
        file_urls=[f"/downloads/{entry.id}/files/{index}" for index, _ in enumerate(entry.files)],
    )
```

Replace the module-private `_display_path` with the shared `display_path` throughout the file, and delete `_display_path`.

Apply the mirror-image change to `app/instagram/api/downloads.py`: same `Query(alias="async")` parameter, `DownloadPlatform.instagram`, and a `_synchronous_response` that also sets `zip_url=f"/instagram/downloads/{entry.id}/zip"`. Do **not** add a `GET ""` list route there — one list serves both platforms.

- [ ] **Step 5: Wire it up in `main.py`**

In `app/main.py`, import the service:

```python
from app.services.download_job_service import DownloadJobService
```

After `instagram_download_service` is constructed:

```python
    # Anything still marked queued or running belongs to a process that is gone.
    orphaned = download_store.fail_orphaned_jobs()
    if orphaned:
        logging.getLogger(__name__).info("Failed %s download(s) orphaned by a restart", orphaned)
    download_job_service = DownloadJobService(
        download_store,
        post_download_service,
        instagram_download_service,
        max_workers=settings.max_concurrent_downloads,
    )
```

Register it on state beside the others:

```python
    app.state.download_job_service = download_job_service
```

And add it to the `services` block of `/health/details`:

```python
                "downloads": download_job_service.diagnostics(),
```

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_download_api -v`
Expected: PASS, 7 tests

- [ ] **Step 7: Run the whole suite**

Run: `.venv/bin/python -m unittest discover -s tests`
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add app/models/download.py app/api/downloads.py app/instagram/api/downloads.py app/main.py tests/test_download_api.py
git commit -m "feat: open an async download door and list in-flight work"
```

---

### Task 6: Cleanup tolerates work that has no files

**Files:**
- Modify: `app/services/cleanup_service.py`
- Test: `tests/test_cleanup_service.py`

**Interfaces:**
- Consumes: `DownloadEntry`, `DownloadStatus` (Task 1).
- Produces: no new public API. `_sweep_expired_downloads`, `_claimed_paths` and a new `_sweep_dead_downloads` all skip entries with an empty `output_dir`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cleanup_service.py`:

```python
class CleanupToleratesJobsTests(CleanupSweepTests):
    def test_a_queued_entry_has_no_files_and_must_not_break_the_sweep(self) -> None:
        """`Path("").resolve()` is the working directory, not nothing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, _, download_store, service = self.build(root)
            download_store.save_entry(
                DownloadEntry(
                    id="queued-1",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.queued,
                    url="https://www.tiktok.com/@a/video/1",
                )
            )

            result = service.sweep()

            self.assertEqual(result["expired_downloads"], 0)
            self.assertIsNotNone(download_store.get_entry("queued-1"))

    def test_a_running_download_is_never_swept_as_an_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, _, download_store, service = self.build(root)
            live_dir = settings.output_dir / "posts" / "running-1"
            live_dir.mkdir(parents=True)
            (live_dir / "part.mp4").write_bytes(b"x")
            self.age(live_dir / "part.mp4", 24 * 30)
            self.age(live_dir, 24 * 30)
            download_store.save_entry(
                DownloadEntry(
                    id="running-1",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.running,
                    output_dir=str(live_dir),
                )
            )

            service.sweep()

            self.assertTrue(live_dir.exists(), "work in flight is claimed, however old its files look")

    def test_a_failed_entry_eventually_leaves_the_register(self) -> None:
        """It has no files and no fetched_at, so nothing else would ever remove it."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, download_store, service = self.build(root)
            download_store.save_entry(
                DownloadEntry(
                    id="failed-1",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.failed,
                    error="that post is gone",
                    created_at=utc_now() - timedelta(hours=48),
                    finished_at=utc_now() - timedelta(hours=48),
                )
            )

            result = service.sweep()

            self.assertEqual(result["dead_downloads"], 1)
            self.assertIsNone(download_store.get_entry("failed-1"))

    def test_a_recent_failure_stays_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, download_store, service = self.build(root)
            download_store.save_entry(
                DownloadEntry(
                    id="failed-2",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.failed,
                    error="that post is gone",
                    finished_at=utc_now(),
                )
            )

            service.sweep()

            self.assertIsNotNone(download_store.get_entry("failed-2"))
```

Add `DownloadStatus` to that file's `app.models.download` import.

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_cleanup_service -v`
Expected: FAIL with `KeyError: 'dead_downloads'`

- [ ] **Step 3: Guard the sweep**

In `app/services/cleanup_service.py`, change `sweep` to include the new counter:

```python
    def sweep(self) -> dict[str, int]:
        result = {
            "expired_downloads": self._sweep_expired_downloads(),
            "dead_downloads": self._sweep_dead_downloads(),
            "expired_recordings": self._sweep_expired_recordings(),
            "orphans_removed": self._sweep_orphans(),
            "logs_removed": self._sweep_logs(),
        }
```

Guard `_sweep_expired_downloads`:

```python
    def _sweep_expired_downloads(self) -> int:
        removed = 0
        for entry in self.download_store.list_entries():
            if not entry.output_dir:
                continue
            if not self.policy.is_expired(entry.fetched_at, self.policy.fetched_hours):
                continue
            shutil.rmtree(Path(entry.output_dir), ignore_errors=True)
            self.download_store.delete_entry(entry.id)
            removed += 1
        return removed
```

Add the new sweep beside it:

```python
    def _sweep_dead_downloads(self) -> int:
        """Failed jobs left nothing on disk, so no other rule reaches them.

        Without this the register accumulates every dead link ever pasted. The
        orphan window is the right clock: it is already "how long we keep
        something nobody is waiting for".
        """
        removed = 0
        for entry in self.download_store.list_entries():
            if entry.status != DownloadStatus.failed or entry.files:
                continue
            stamped = entry.finished_at or entry.created_at
            if not self.policy.is_expired(stamped, self.policy.orphan_hours):
                continue
            self.download_store.delete_entry(entry.id)
            removed += 1
        return removed
```

Guard `_claimed_paths` so an empty `output_dir` never resolves to the working directory:

```python
            for entry in self.download_store.list_entries():
                if entry.output_dir:
                    claimed.add(str(Path(entry.output_dir).resolve()))
                claimed.update(str(Path(path).resolve()) for path in entry.files)
```

Add `DownloadStatus` to the imports:

```python
from app.models.download import DownloadStatus
```

- [ ] **Step 4: Run the tests**

Run: `.venv/bin/python -m unittest tests.test_cleanup_service -v`
Expected: PASS

- [ ] **Step 5: Run the whole suite**

Run: `.venv/bin/python -m unittest discover -s tests`
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add app/services/cleanup_service.py tests/test_cleanup_service.py
git commit -m "fix: teach the sweep about downloads that have no files yet"
```

---

### Task 7: The web UI stacks submissions

**Files:**
- Modify: `app/static/js/save-page.js`
- Modify: `app/templates/download.html`
- Modify: `app/templates/base.html` (asset version only, if `app.css` changes)

**Interfaces:**
- Consumes: `POST /downloads?async=1`, `POST /instagram/downloads?async=1`, `GET /downloads` (Task 5).
- Produces: no new element ids. `#post-download-result` becomes a list container instead of a single-result slot.

Behaviour to build:

1. Submitting posts to the async door, clears the input, and does **not** disable the button. Paste, save, paste again.
2. The Filed list renders every entry from `GET /downloads` as a `.job-card`, with the stamp classes that already exist: `Queued`/`Working` → `.stamp.soft`, `Filed` → `.stamp.good`, `Failed` → `.stamp.bad`. A running card also gets `.job-card.live`, which the CSS already animates.
3. Polling follows `record-page.js` exactly, including `POLL_FAILURES_BEFORE_WARNING = 3` — a single dropped poll must never replace a real message with "Failed to fetch".
4. Poll every 2s while anything is `queued` or `running`, every 10s otherwise.
5. `data-series="ig"` on Instagram cards, so the second ink is applied by the CSS that already exists.

- [ ] **Step 1: Rewrite `save-page.js`**

```javascript
/**
 * One page for both platforms, and one register for both.
 *
 * The endpoint is chosen from the link's hostname — the same rule the backend
 * validators and the Android UrlRouter already use — so the person pasting a
 * link never has to know which app they are "in".
 *
 * Submissions stack. The old page disabled the button for the length of a
 * download and kept one result slot, which is where "downloads run one at a
 * time" came from: the server never serialized anything.
 */
function initSavePage() {
  const form = document.getElementById("post-download-form");
  const urlInput = document.getElementById("post-url");
  const notice = document.getElementById("post-download-notice");
  const resultContainer = document.getElementById("post-download-result");
  const clearButton = document.getElementById("clear-post-download-form");

  const ACTIVE_POLL_MS = 2000;
  const IDLE_POLL_MS = 10000;
  const POLL_FAILURES_BEFORE_WARNING = 3;

  let pollTimer = null;
  let currentPollMs = null;
  let consecutivePollFailures = 0;
  let pollWarningShown = false;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;").replaceAll("'", "&#039;");
  }

  function hostOf(rawUrl) {
    const trimmed = rawUrl.trim();
    // People paste "www.tiktok.com/@a/video/1" as often as the full link, and
    // the old page accepted it. new URL() needs a scheme, so assume https.
    for (const candidate of [trimmed, `https://${trimmed}`]) {
      try { return new URL(candidate).hostname.toLowerCase(); } catch { /* try next */ }
    }
    return null;
  }

  function platformFor(rawUrl) {
    const host = hostOf(rawUrl);
    if (!host) return null;
    if (host === "tiktok.com" || host.endsWith(".tiktok.com")) return "tiktok";
    if (host === "instagram.com" || host.endsWith(".instagram.com") || host === "instagr.am") return "instagram";
    return null;
  }

  const endpointFor = (platform) => (platform === "instagram" ? "/instagram/downloads" : "/downloads");
  const basePathFor = (platform) => (platform === "instagram" ? "/instagram/downloads" : "/downloads");
  const fileName = (path) => String(path ?? "").split(/[\\/]/).pop() || "download";
  const isInstagram = (entry) => entry.platform === "instagram";
  const isActive = (entry) => entry.status === "queued" || entry.status === "running";

  function stampFor(entry) {
    if (entry.status === "queued") return { label: "Queued", cls: "soft" };
    if (entry.status === "running") return { label: "Working", cls: "soft" };
    if (entry.status === "failed") return { label: "Failed", cls: "bad" };
    return { label: "Filed", cls: "good" };
  }

  function titleFor(entry) {
    if (entry.status === "queued") return "Waiting for a slot";
    if (entry.status === "running") return "Working…";
    if (entry.status === "failed") return "Could not be filed";
    const count = (entry.files || []).length;
    return `${count} file${count === 1 ? "" : "s"} filed`;
  }

  function retentionNote(entry) {
    if (!entry.fetched_at) return "";
    const removesAt = new Date(new Date(entry.fetched_at).getTime() + 24 * 3600 * 1000);
    const hoursLeft = Math.max(0, Math.round((removesAt - Date.now()) / 3600000));
    return `Taken — removed in ~${hoursLeft}h`;
  }

  function emptyState() {
    return `<div class="empty"><span class="empty-title">Nothing filed yet</span>
      <span>Paste a TikTok or Instagram link above.</span></div>`;
  }

  function renderCard(entry) {
    const stamp = stampFor(entry);
    const files = entry.files || [];
    const fileUrls = entry.file_urls || [];
    const rows = files.map((path, index) => `
      <div class="file-row">
        <div class="file-meta">
          <span class="file-name">${escapeHtml(fileName(path))}</span>
          <span class="file-path">${escapeHtml(path)}</span>
        </div>
        <a class="btn btn-sm" href="${appPath(fileUrls[index])}">Take a copy</a>
      </div>`).join("");

    const zip = entry.zip_url
      ? `<a class="btn btn-sm btn-quiet" href="${appPath(entry.zip_url)}">Take all as zip</a>` : "";
    const message = entry.status === "failed"
      ? `<p class="job-message">${escapeHtml(entry.error || "The download failed.")}</p>`
      : entry.status === "running"
        ? `<p class="job-message">Asking ${isInstagram(entry) ? "Instagram" : "TikTok"} for the media.</p>`
        : entry.status === "queued"
          ? `<p class="job-message">Two downloads run at a time. This one starts when a slot frees.</p>`
          : "";
    const note = retentionNote(entry);

    return `
      <article class="job-card${isActive(entry) ? " live" : ""}" data-series="${isInstagram(entry) ? "ig" : "tt"}">
        <div class="job-header">
          <div>
            <span class="job-id">No. ${escapeHtml(entry.id)} · ${escapeHtml(isInstagram(entry) ? "instagram" : "tiktok")}</span>
            <h3 class="job-title">${escapeHtml(titleFor(entry))}</h3>
          </div>
          <span class="stamp ${stamp.cls}">${escapeHtml(stamp.label)}</span>
        </div>
        ${message}
        ${rows ? `<div class="file-list">${rows}</div>` : ""}
        ${note ? `<p class="retention">${escapeHtml(note)}</p>` : ""}
        <div class="job-actions">${zip}
          <button class="btn btn-sm btn-danger" data-action="delete-download"
                  data-id="${escapeHtml(entry.id)}"
                  data-platform="${escapeHtml(isInstagram(entry) ? "instagram" : "tiktok")}">Discard</button>
        </div>
      </article>`;
  }

  function render(entries) {
    resultContainer.innerHTML = entries.length
      ? entries.map(renderCard).join("")
      : emptyState();
    setPollRate(entries.some(isActive) ? ACTIVE_POLL_MS : IDLE_POLL_MS);
  }

  // A background poll must not clobber the notice describing what the user just
  // did: a single dropped request used to replace a real message with the
  // browser's raw "Failed to fetch".
  function onPollSuccess() {
    consecutivePollFailures = 0;
    if (pollWarningShown) {
      pollWarningShown = false;
      setNotice(notice, "Reconnected.");
    }
  }

  function onPollFailure() {
    consecutivePollFailures += 1;
    if (consecutivePollFailures < POLL_FAILURES_BEFORE_WARNING) return;
    pollWarningShown = true;
    setNotice(notice, "Lost connection to the app — still retrying…", "error");
  }

  async function fetchDownloads() {
    const response = await fetch(appPath("/downloads"));
    if (!response.ok) throw new Error(`Failed to load the register: ${response.status}`);
    render(await response.json());
  }

  function pollOnce() {
    return fetchDownloads().then(onPollSuccess, onPollFailure);
  }

  function setPollRate(intervalMs) {
    if (currentPollMs === intervalMs) return;
    currentPollMs = intervalMs;
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = window.setInterval(pollOnce, intervalMs);
  }

  async function submitDownload(event) {
    event.preventDefault();
    const url = urlInput.value.trim();
    const platform = platformFor(url);
    if (!platform) {
      setNotice(notice, "That does not look like a TikTok or Instagram link.", "error");
      return;
    }
    try {
      const response = await fetch(appPath(`${endpointFor(platform)}?async=1`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      if (!response.ok) throw new Error(await readApiError(response, "The download could not be started."));
      await response.json();
      // Clear on submit so the next link can be pasted immediately.
      urlInput.value = "";
      setNotice(notice, "Entered in the register. Paste another if you like.");
      await fetchDownloads();
    } catch (error) {
      setNotice(notice, error.message, "error");
    }
  }

  resultContainer.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action='delete-download']");
    if (!button) return;
    button.disabled = true;
    try {
      const base = basePathFor(button.dataset.platform);
      const response = await fetch(appPath(`${base}/${button.dataset.id}`), { method: "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "Could not discard it."));
      setNotice(notice, "Discarded from the server.");
      await fetchDownloads();
    } catch (error) {
      button.disabled = false;
      setNotice(notice, error.message, "error");
    }
  });

  form.addEventListener("submit", submitDownload);
  clearButton.addEventListener("click", () => {
    form.reset();
    setNotice(notice, "Cleared.");
  });

  resultContainer.innerHTML = emptyState();
  setPollRate(IDLE_POLL_MS);
  pollOnce();
}
```

- [ ] **Step 2: Bump the asset version**

In `app/templates/download.html`, change `save-page.js?v=1` to `save-page.js?v=2`.

- [ ] **Step 3: Update the page copy**

In `app/templates/download.html`, replace the lede and the notice so the page no longer implies one-at-a-time:

```html
  <p class="lede">Paste a TikTok or Instagram link. The register works out which is which and files the media here until you take it. Paste as many as you like — two are fetched at a time and the rest wait their turn.</p>
```

```html
  <div id="post-download-notice" class="notice">Paste a link and the register will do the rest.</div>
```

- [ ] **Step 4: Verify in the browser**

Start the dev server and check the Save post page: submit a link, confirm the input clears, the button stays enabled, a `Queued`/`Working` card appears without a page reload, and the console is clean.

Run: `.venv/bin/python -m uvicorn app.main:app --reload --port 8000`
Expected: a card appears in the Filed list within 2 seconds of submitting, and a second submission adds a second card rather than replacing the first.

- [ ] **Step 5: Commit**

```bash
git add app/static/js/save-page.js app/templates/download.html
git commit -m "ui: stack submissions and poll the register"
```

---

### Task 8: Record the deletion trigger

**Files:**
- Modify: `SSH.md`

The synchronous door and the `/tiktok` nginx prefix are both shims with the same
trigger. `SSH.md` already carries the `/tiktok` retirement note; the synchronous
door belongs beside it or it will be forgotten.

- [ ] **Step 1: Add the note**

Under the existing "Retiring `/tiktok`" section in `SSH.md`, add:

```markdown
### Retiring the synchronous download door

`POST /downloads` and `POST /instagram/downloads` without `?async=1` submit a job
and hold the connection open until it finishes. That exists only because the
shipped Android build (`com.ttldownloader.app`, versionCode 5) posts and waits.

**Delete both synchronous branches once Still Here mobile ships against the
async door** — same trigger as the `/tiktok` prefix above, and worth doing in
the same change.

Until then, `/stillhere/` and `/tiktok/` need `proxy_read_timeout` raised: nginx
defaults to 60 seconds, so any download slower than that already 504s. The
pattern is in the `/breaking-bad/` block on the same box, which sets
`proxy_read_timeout 86400`.
```

- [ ] **Step 2: Commit**

```bash
git add SSH.md
git commit -m "docs: record when the synchronous download door can go"
```

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
|---|---|
| Job model — lifecycle fields on `DownloadEntry`, docstring update | 1 |
| Worker pool — `DownloadJobService`, `max_concurrent_downloads` | 4 |
| Fetch logic unchanged | 3 (only an optional id parameter) |
| `POST /downloads` unchanged | 5 |
| `POST /downloads?async=1` | 5 |
| `GET /downloads` lists in-flight | 5 |
| `POST /instagram/downloads` mirrors | 5 |
| Web UI: form clears, button stays enabled | 7 |
| Web UI: `.job-card` + `.stamp` reuse | 7 |
| Web UI: `record-page.js` poll tolerance | 7 |
| Edge: restart orphans → failed | 2 (store), 5 (called at startup) |
| Edge: cleanup skips fileless entries | 6 |
| Edge: Instagram fetch-once not shown as live | 7 (retention note) |
| Edge: failures are per-job | 4 (test), 7 (per-card error) |
| Edge: duplicate submissions allowed | 4 (no dedup written) |
| Testing: all six listed assertions | 4, 5, 6 |
| Rollout: deletion trigger recorded | 8 |

**Deliberate additions beyond the spec**, both small and both justified inline:
`_sweep_dead_downloads` (Task 6) — without it the register accumulates every
dead link ever pasted, since a failed entry has neither files nor `fetched_at`;
and `display_path` / `new_download_id` moved into `app/models/download.py`,
which removes a duplicated helper from each of the four call sites.

**Not in this plan, by the spec's own scope:** the nginx `proxy_read_timeout`
change (a server-side edit, recorded in Task 8), batch multi-URL paste,
download prioritisation, retry-on-failure, and per-download progress
percentages.
