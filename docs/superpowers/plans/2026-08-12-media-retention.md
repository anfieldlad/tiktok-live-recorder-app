# Media Retention Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Media is deleted only after the user has been given it (plus a grace period), by explicit request, or because nothing references it — never because it merely got old.

**Architecture:** Downloads gain a persisted index (`DownloadStore`, mirroring the existing `JobStore`) so they survive restarts and can always be told apart from leftovers. Serving a file stamps `fetched_at` instead of deleting it. A rewritten `CleanupService`, driven by a `RetentionPolicy` dataclass, removes only fetched-and-expired entries and unreferenced orphans.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, `unittest` (stdlib), vanilla JS frontend.

## Global Constraints

- Design source: `docs/superpowers/specs/2026-08-12-media-retention-design.md`.
- **An item with `fetched_at: null` is never swept, at any age.** This is a rule, not a duration, and must not become configurable.
- Every duration is a named setting; no sweep code contains a literal number of hours.
- `CLEANUP_MAX_AGE_HOURS` remains supported as the fallback for both retention windows, so the deployed `.env` keeps working untouched.
- Follow the existing small-service layout in `app/services/`; wire dependencies through `create_app()` and `app.state`.
- Prefer `pathlib.Path`. Keep API errors structured, as existing routes do.
- Never log or expose cookie/session values.
- Test command: `.venv/bin/python -m unittest discover -s tests`. **Note:** the repo `.venv` on this Mac is broken (it points at a removed Homebrew python@3.13). Rebuild with `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`, or use any Python 3.11+ interpreter with the requirements installed.
- Commit after every task. Do not deploy; deployment is a separate, explicit step.

---

### Task 1: Retention parameters

**Files:**
- Modify: `app/services/config.py`
- Create: `app/services/retention.py`
- Test: `tests/test_retention.py`
- Modify: `.env.example`, `README.md`

**Interfaces:**
- Consumes: `Settings` from `app/services/config.py`.
- Produces: `RetentionPolicy` dataclass with float fields `fetched_hours`, `orphan_hours`, `log_hours`, `interval_seconds`, `storage_soft_limit_bytes`; classmethod `RetentionPolicy.from_settings(settings) -> RetentionPolicy`; method `is_expired(self, timestamp: datetime | None, hours: float) -> bool` returning `False` when `timestamp is None`; method `is_older_than(self, mtime: float, hours: float) -> bool` for filesystem mtimes (epoch seconds), used by the orphan sweep.

- [ ] **Step 1: Write the failing test**

Create `tests/test_retention.py`:

```python
from __future__ import annotations

import os
import unittest
from datetime import timedelta

from app.models.recording import utc_now
from app.services.config import Settings
from app.services.retention import RetentionPolicy


class RetentionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        for key in (
            "RETENTION_FETCHED_HOURS",
            "RETENTION_ORPHAN_HOURS",
            "CLEANUP_MAX_AGE_HOURS",
            "LOG_MAX_AGE_HOURS",
            "STORAGE_SOFT_LIMIT_GB",
        ):
            os.environ.pop(key, None)
            self.addCleanup(os.environ.pop, key, None)

    def test_defaults(self) -> None:
        policy = RetentionPolicy.from_settings(Settings())

        self.assertEqual(policy.fetched_hours, 24)
        self.assertEqual(policy.orphan_hours, 24)
        self.assertEqual(policy.log_hours, 72)
        self.assertEqual(policy.storage_soft_limit_bytes, 20 * 1024**3)

    def test_cleanup_max_age_is_the_fallback_for_both_windows(self) -> None:
        os.environ["CLEANUP_MAX_AGE_HOURS"] = "6"

        policy = RetentionPolicy.from_settings(Settings())

        self.assertEqual(policy.fetched_hours, 6)
        self.assertEqual(policy.orphan_hours, 6)

    def test_explicit_windows_win_over_the_fallback(self) -> None:
        os.environ["CLEANUP_MAX_AGE_HOURS"] = "6"
        os.environ["RETENTION_FETCHED_HOURS"] = "48"

        policy = RetentionPolicy.from_settings(Settings())

        self.assertEqual(policy.fetched_hours, 48)
        self.assertEqual(policy.orphan_hours, 6)

    def test_never_fetched_never_expires(self) -> None:
        policy = RetentionPolicy.from_settings(Settings())

        self.assertFalse(policy.is_expired(None, policy.fetched_hours))

    def test_expiry_is_measured_from_the_timestamp(self) -> None:
        policy = RetentionPolicy.from_settings(Settings())
        just_now = utc_now()
        long_ago = utc_now() - timedelta(hours=25)

        self.assertFalse(policy.is_expired(just_now, policy.fetched_hours))
        self.assertTrue(policy.is_expired(long_ago, policy.fetched_hours))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_retention -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.retention'`

- [ ] **Step 3: Add the settings**

In `app/services/config.py`, replace the cleanup settings block:

```python
    # 24h, not 3: downloads and orphans are cheap to keep and expensive to lose.
    # A short window cost a finished recording once already.
    cleanup_max_age_hours: int = 24
    cleanup_interval_minutes: int = 30
    log_max_age_hours: int = 72
```

with:

```python
    # 24h, not 3: downloads and orphans are cheap to keep and expensive to lose.
    # A short window cost a finished recording once already. Kept as the
    # fallback for the two retention windows below so existing .env files work.
    cleanup_max_age_hours: int = 24
    # How long a *fetched* item lingers before the sweep removes it, and how
    # long an unreferenced leftover survives. None means "use the fallback".
    retention_fetched_hours: float | None = None
    retention_orphan_hours: float | None = None
    cleanup_interval_minutes: int = 30
    log_max_age_hours: int = 72
    storage_soft_limit_gb: float = 20
```

- [ ] **Step 4: Write the policy**

Create `app/services/retention.py`:

```python
"""Every duration the cleanup sweep obeys, in one place.

Gathering them here keeps hour literals out of the sweep and lets a test build
a policy with second-scale windows instead of back-dating file mtimes.

One rule is deliberately absent: an item that was never fetched is never
swept. That is not a duration and must not become configurable — a config
mistake should not be able to destroy media the user has not seen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.recording import utc_now
from app.services.config import Settings


@dataclass(frozen=True)
class RetentionPolicy:
    fetched_hours: float
    orphan_hours: float
    log_hours: float
    interval_seconds: int
    storage_soft_limit_bytes: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetentionPolicy":
        fallback = float(settings.cleanup_max_age_hours)
        return cls(
            fetched_hours=float(settings.retention_fetched_hours or fallback),
            orphan_hours=float(settings.retention_orphan_hours or fallback),
            log_hours=float(settings.log_max_age_hours),
            interval_seconds=int(settings.cleanup_interval_minutes * 60),
            storage_soft_limit_bytes=int(settings.storage_soft_limit_gb * 1024**3),
        )

    def is_expired(self, timestamp: datetime | None, hours: float) -> bool:
        """True only when `timestamp` is set and older than `hours`."""
        if timestamp is None:
            return False
        return timestamp < utc_now() - timedelta(hours=hours)

    def is_older_than(self, mtime: float, hours: float) -> bool:
        """True when a filesystem mtime (epoch seconds) is older than `hours`."""
        return mtime < (utc_now() - timedelta(hours=hours)).timestamp()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_retention -v`
Expected: PASS, 5 tests

- [ ] **Step 6: Document the settings**

Append to `.env.example`:

```env
RETENTION_FETCHED_HOURS=24
RETENTION_ORPHAN_HOURS=24
STORAGE_SOFT_LIMIT_GB=20
```

In `README.md`, in the production `.env` example block, add the same three lines after `CLEANUP_MAX_AGE_HOURS=24`, then add this paragraph directly below that block:

```markdown
Retention is event-driven: media is removed once you have downloaded it plus
`RETENTION_FETCHED_HOURS`, or when you delete it, or when it is an orphan no
record references (`RETENTION_ORPHAN_HOURS`). Anything you have never fetched
is kept indefinitely, whatever those values are.
```

- [ ] **Step 7: Commit**

```bash
git add app/services/config.py app/services/retention.py tests/test_retention.py .env.example README.md
git commit -m "retention: put every cleanup window behind one named policy"
```

---

### Task 2: DownloadStore

**Files:**
- Create: `app/models/download.py`
- Create: `app/services/download_store.py`
- Test: `tests/test_download_store.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - `DownloadPlatform` str-Enum with members `tiktok_post = "tiktok_post"` and `instagram = "instagram"`.
  - `DownloadEntry` Pydantic model: `id: str`, `platform: DownloadPlatform`, `output_dir: str`, `files: list[str]`, `created_at: datetime`, `fetched_at: datetime | None = None`.
  - `DownloadStore(downloads_file: Path)` with `list_entries() -> list[DownloadEntry]`, `get_entry(download_id) -> DownloadEntry | None`, `save_entry(entry) -> DownloadEntry`, `mark_fetched(download_id) -> DownloadEntry | None`, `delete_entry(download_id) -> bool`, `diagnostics() -> dict[str, object]`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_download_store.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models.download import DownloadEntry, DownloadPlatform
from app.services.download_store import DownloadStore


def make_entry(store_dir: Path, entry_id: str = "20260812-101500-abc123") -> DownloadEntry:
    return DownloadEntry(
        id=entry_id,
        platform=DownloadPlatform.tiktok_post,
        output_dir=str(store_dir / "posts" / entry_id),
        files=[str(store_dir / "posts" / entry_id / "video.mp4")],
    )


class DownloadStoreTests(unittest.TestCase):
    def test_entries_survive_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "downloads.json"
            DownloadStore(path).save_entry(make_entry(root))

            # A new instance is what a process restart looks like.
            reopened = DownloadStore(path)

            entries = reopened.list_entries()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].id, "20260812-101500-abc123")
            self.assertIsNone(entries[0].fetched_at)

    def test_mark_fetched_stamps_once_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "downloads.json")
            store.save_entry(make_entry(root))

            first = store.mark_fetched("20260812-101500-abc123")
            second = store.mark_fetched("20260812-101500-abc123")

            self.assertIsNotNone(first.fetched_at)
            self.assertIsNotNone(second.fetched_at)
            self.assertGreaterEqual(second.fetched_at, first.fetched_at)

    def test_mark_fetched_on_unknown_id_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DownloadStore(Path(temp_dir) / "downloads.json")

            self.assertIsNone(store.mark_fetched("nope"))

    def test_delete_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "downloads.json")
            store.save_entry(make_entry(root))

            self.assertTrue(store.delete_entry("20260812-101500-abc123"))
            self.assertFalse(store.delete_entry("20260812-101500-abc123"))
            self.assertEqual(store.list_entries(), [])

    def test_corrupt_file_is_backed_up_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "downloads.json"
            path.write_text("{broken", encoding="utf-8")

            store = DownloadStore(path)

            self.assertEqual(store.list_entries(), [])
            self.assertEqual(path.read_text(encoding="utf-8"), "[]\n")
            self.assertEqual(len(list(root.glob("downloads.corrupt-*.json"))), 1)
            self.assertEqual(store.diagnostics()["recovery_count"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_download_store -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.models.download'`

- [ ] **Step 3: Write the model**

Create `app/models/download.py`:

```python
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.recording import utc_now


class DownloadPlatform(str, Enum):
    tiktok_post = "tiktok_post"
    instagram = "instagram"


class DownloadEntry(BaseModel):
    """One completed download, and whether the user has been given it yet.

    `fetched_at` is the whole point of persisting this: without it, a restart
    left files on disk that nothing could serve and nothing would remove.
    """

    id: str
    platform: DownloadPlatform
    output_dir: str
    files: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    fetched_at: Optional[datetime] = None
```

- [ ] **Step 4: Write the store**

Create `app/services/download_store.py`. It mirrors `app/services/job_store.py` deliberately — same locking, same atomic write, same corrupt-file recovery — so there is one storage pattern in this codebase rather than two:

```python
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

from app.models.download import DownloadEntry
from app.models.recording import utc_now


logger = logging.getLogger(__name__)


class DownloadStore:
    """Persisted index of completed downloads.

    Mirrors JobStore on purpose: atomic replace through a temp file, and a
    corrupt file is backed aside rather than losing the index.
    """

    def __init__(self, downloads_file: Path) -> None:
        self.downloads_file = downloads_file
        self._lock = threading.RLock()
        self._recovery_count = 0
        self._last_recovery_at: str | None = None
        self.downloads_file.parent.mkdir(parents=True, exist_ok=True)
        if not self.downloads_file.exists():
            self.downloads_file.write_text("[]\n", encoding="utf-8")

    def list_entries(self) -> list[DownloadEntry]:
        with self._lock:
            return sorted(self._read(), key=lambda item: item.created_at, reverse=True)

    def get_entry(self, download_id: str) -> DownloadEntry | None:
        with self._lock:
            return next((entry for entry in self._read() if entry.id == download_id), None)

    def save_entry(self, entry: DownloadEntry) -> DownloadEntry:
        with self._lock:
            entries = self._read()
            for index, existing in enumerate(entries):
                if existing.id == entry.id:
                    entries[index] = entry
                    break
            else:
                entries.append(entry)
            self._write(entries)
        return entry

    def mark_fetched(self, download_id: str) -> DownloadEntry | None:
        """Record that the user has been given this download."""
        with self._lock:
            entries = self._read()
            for index, entry in enumerate(entries):
                if entry.id == download_id:
                    entries[index] = entry.model_copy(update={"fetched_at": utc_now()})
                    self._write(entries)
                    return entries[index]
        return None

    def delete_entry(self, download_id: str) -> bool:
        with self._lock:
            entries = self._read()
            remaining = [entry for entry in entries if entry.id != download_id]
            if len(remaining) == len(entries):
                return False
            self._write(remaining)
            return True

    def diagnostics(self) -> dict[str, object]:
        return {
            "downloads_file": str(self.downloads_file),
            "recovery_count": self._recovery_count,
            "last_recovery_at": self._last_recovery_at,
        }

    def _read(self) -> list[DownloadEntry]:
        try:
            raw = self.downloads_file.read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            logger.exception("Failed to read downloads file")
            raise RuntimeError(f"failed to read downloads file: {exc}") from exc
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError("downloads file must contain a JSON array")
            return [DownloadEntry.model_validate(item) for item in data]
        except Exception:
            logger.exception("Recovering corrupt downloads file")
            self._recover(raw)
            return []

    def _write(self, entries: list[DownloadEntry]) -> None:
        payload = [entry.model_dump(mode="json") for entry in entries]
        temp_file = self.downloads_file.with_name(f"{self.downloads_file.name}.tmp")
        temp_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        temp_file.replace(self.downloads_file)

    def _recover(self, raw: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = self.downloads_file.with_name(
            f"{self.downloads_file.stem}.corrupt-{timestamp}{self.downloads_file.suffix}"
        )
        try:
            if raw.strip():
                backup.write_text(raw if raw.endswith("\n") else raw + "\n", encoding="utf-8")
        except OSError:
            logger.exception("Failed to back up corrupt downloads file")
        self.downloads_file.write_text("[]\n", encoding="utf-8")
        self._recovery_count += 1
        self._last_recovery_at = datetime.now(timezone.utc).isoformat()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_download_store -v`
Expected: PASS, 5 tests

- [ ] **Step 6: Commit**

```bash
git add app/models/download.py app/services/download_store.py tests/test_download_store.py
git commit -m "downloads: persist completed downloads so a restart cannot orphan them"
```

---

### Task 3: Download services use the store

**Files:**
- Modify: `app/services/post_download_service.py`
- Modify: `app/instagram/services/instagram_download_service.py`
- Modify: `app/main.py`
- Modify: `app/services/config.py`
- Test: `tests/test_download_services.py`

**Interfaces:**
- Consumes: `DownloadStore`, `DownloadEntry`, `DownloadPlatform` (Task 2).
- Produces:
  - `Settings.downloads_file: Path` defaulting to `PROJECT_ROOT / "data" / "downloads.json"`, created by `ensure_directories()`.
  - `PostDownloadService(output_dir, cookie_service=None, download_store=None)` and `InstagramDownloadService(output_dir, cookie_service=None, download_store=None)`.
  - Both keep `get_result(download_id)` and `resolve_file(download_id, index)` with unchanged signatures; both now read through the store when one was supplied.
  - `InstagramDownloadService.cleanup_file_after_download` and `cleanup_after_archive` are **removed** — Task 4 replaces their call sites.
  - `app.state.download_store` is the shared `DownloadStore`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_download_services.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.download_store import DownloadStore
from app.services.post_download_service import PostDownloadResult, PostDownloadService


class PostDownloadServicePersistenceTests(unittest.TestCase):
    def test_results_are_readable_after_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "data" / "downloads.json")
            service = PostDownloadService(root / "output", download_store=store)

            download_dir = service.output_dir / "20260812-101500-abc123"
            download_dir.mkdir(parents=True)
            media = download_dir / "video.mp4"
            media.write_bytes(b"x" * 10)
            service.remember(
                PostDownloadResult(
                    download_id="20260812-101500-abc123",
                    output_dir=download_dir,
                    files=[media],
                )
            )

            # A fresh service on the same store is what a restart looks like.
            restarted = PostDownloadService(
                root / "output",
                download_store=DownloadStore(root / "data" / "downloads.json"),
            )

            result = restarted.get_result("20260812-101500-abc123")
            self.assertIsNotNone(result, "a completed download must survive a restart")
            self.assertEqual(restarted.resolve_file("20260812-101500-abc123", 0), media.resolve())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_download_services -v`
Expected: FAIL with `TypeError: PostDownloadService.__init__() got an unexpected keyword argument 'download_store'`

- [ ] **Step 3: Add the settings entry**

In `app/services/config.py`, add below `watch_jobs_file`:

```python
    downloads_file: Path = PROJECT_ROOT / "data" / "downloads.json"
```

and inside `ensure_directories()`, below the `watch_jobs_file` line:

```python
        self.downloads_file.parent.mkdir(parents=True, exist_ok=True)
```

- [ ] **Step 4: Back PostDownloadService with the store**

In `app/services/post_download_service.py`, add the imports:

```python
from app.models.download import DownloadEntry, DownloadPlatform
from app.services.download_store import DownloadStore
```

Replace `__init__` and the result accessors:

```python
    def __init__(
        self,
        output_dir: Path,
        cookie_service: CookieService | None = None,
        download_store: DownloadStore | None = None,
    ) -> None:
        self.output_dir = output_dir / "posts"
        self.cookie_service = cookie_service
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.download_store = download_store
        # Fallback for callers that construct the service without a store
        # (older tests). A restart still loses these, which is exactly the
        # problem the store exists to solve.
        self._results: dict[str, PostDownloadResult] = {}

    def remember(self, result: PostDownloadResult) -> PostDownloadResult:
        """Record a completed download so it can be served and, later, swept."""
        if self.download_store is not None:
            self.download_store.save_entry(
                DownloadEntry(
                    id=result.download_id,
                    platform=DownloadPlatform.tiktok_post,
                    output_dir=str(result.output_dir),
                    files=[str(path) for path in result.files],
                )
            )
        else:
            self._results[result.download_id] = result
        return result

    def get_result(self, download_id: str) -> PostDownloadResult | None:
        if self.download_store is None:
            return self._results.get(download_id)
        entry = self.download_store.get_entry(download_id)
        if entry is None:
            return None
        return PostDownloadResult(
            download_id=entry.id,
            output_dir=Path(entry.output_dir),
            files=[Path(path) for path in entry.files],
        )
```

Then replace both `self._results[download_id] = ...` assignments in `download()` with `self.remember(...)`:

```python
                if fallback_result is not None:
                    return self.remember(fallback_result)
```

and:

```python
        download_result = PostDownloadResult(download_id=download_id, output_dir=download_dir, files=files)
        return self.remember(download_result)
```

- [ ] **Step 5: Back InstagramDownloadService with the store**

In `app/instagram/services/instagram_download_service.py`, add the imports:

```python
from app.models.download import DownloadEntry, DownloadPlatform
from app.services.download_store import DownloadStore
```

Replace `__init__` and the result accessors:

```python
    def __init__(
        self,
        output_dir: Path,
        cookie_service: InstagramCookieService | None = None,
        download_store: DownloadStore | None = None,
    ) -> None:
        self.output_dir = output_dir / "instagram"
        self.cookie_service = cookie_service
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.download_store = download_store
        self._results: dict[str, InstagramDownloadResult] = {}

    def remember(self, result: InstagramDownloadResult) -> InstagramDownloadResult:
        """Record a completed download so it can be served and, later, swept."""
        if self.download_store is not None:
            self.download_store.save_entry(
                DownloadEntry(
                    id=result.download_id,
                    platform=DownloadPlatform.instagram,
                    output_dir=str(result.output_dir),
                    files=[str(path) for path in result.files],
                )
            )
        else:
            self._results[result.download_id] = result
        return result

    def get_result(self, download_id: str) -> InstagramDownloadResult | None:
        if self.download_store is None:
            return self._results.get(download_id)
        entry = self.download_store.get_entry(download_id)
        if entry is None:
            return None
        return InstagramDownloadResult(
            download_id=entry.id,
            output_dir=Path(entry.output_dir),
            files=[Path(path) for path in entry.files],
        )
```

In `download()`, replace:

```python
        download_result = InstagramDownloadResult(download_id=download_id, output_dir=download_dir, files=files)
        self._results[download_id] = download_result
        return download_result
```

with:

```python
        return self.remember(
            InstagramDownloadResult(download_id=download_id, output_dir=download_dir, files=files)
        )
```

Then **delete** the `cleanup_file_after_download` and `cleanup_after_archive` methods entirely. Their behaviour moves to Task 4; leaving them would give two places that delete media. `create_archive` keeps working unchanged — it reads through `get_result`.

- [ ] **Step 6: Wire it in `create_app()`**

In `app/main.py`, add the import:

```python
from app.services.download_store import DownloadStore
```

Add the store above the services that need it and pass it to both:

```python
    download_store = DownloadStore(settings.downloads_file)
    post_download_service = PostDownloadService(settings.output_dir, cookie_service, download_store)
    ...
    instagram_download_service = InstagramDownloadService(
        settings.output_dir, instagram_cookie_service, download_store
    )
```

and expose it:

```python
    app.state.download_store = download_store
```

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: `tests.test_download_services` passes. Two existing Instagram cleanup tests in `tests/test_app_reliability.py` — `test_metadata_download_keeps_media` and `test_media_download_wipes_whole_download` — now fail with `AttributeError: 'InstagramDownloadService' object has no attribute 'cleanup_file_after_download'`. That is expected: they assert delete-on-serve, which this design replaces. Delete both tests; Task 5 covers the replacement behaviour.

- [ ] **Step 8: Commit**

```bash
git add app/services/post_download_service.py app/instagram/services/instagram_download_service.py app/main.py app/services/config.py tests/test_download_services.py tests/test_app_reliability.py
git commit -m "downloads: read and write completed downloads through the store"
```

---

### Task 4: Serving stamps instead of deleting

**Files:**
- Modify: `app/api/downloads.py`
- Modify: `app/instagram/api/downloads.py`
- Modify: `app/api/recordings.py`
- Modify: `app/services/file_service.py`
- Modify: `app/models/recording.py`
- Test: `tests/test_app_reliability.py`

**Interfaces:**
- Consumes: `DownloadStore.mark_fetched` (Task 2).
- Produces:
  - `RecordingJob.fetched_at: Optional[datetime] = None` and the same field on `RecordingJobResponse` (populated in `from_job`).
  - `FileService.mark_downloaded(job_id: str) -> None` replacing `cleanup_download_artifacts`.
  - New routes `DELETE /downloads/{download_id}` and `DELETE /instagram/downloads/{download_id}`, both returning `{"deleted": true}` with status 200, or 404 when unknown.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_app_reliability.py`, inside `AppReliabilityTests`:

```python
    def test_downloading_a_recording_stamps_it_instead_of_deleting_it(self) -> None:
        """The file must outlive the download: a save interrupted halfway has to
        be retryable, and a phone that drops Wi-Fi should not destroy the only
        copy."""
        from app.models.recording import RecordingJob, RecordingStatus

        client = self.create_test_client()
        job_store = client.app.state.job_store
        settings = client.app.state.settings

        recording = settings.output_dir / "TK_someone_2026.08.12_10-00-00.mp4"
        recording.write_bytes(b"x" * 32)
        job = RecordingJob(
            username="someone",
            status=RecordingStatus.finished,
            file_path=str(recording),
        )
        job_store.save_job(job)

        response = client.get(f"/recordings/{job.id}/download")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(recording.exists(), "the file must survive being downloaded")
        stamped = job_store.get_job(job.id)
        self.assertIsNotNone(stamped, "the job must survive being downloaded")
        self.assertIsNotNone(stamped.fetched_at, "downloading must stamp fetched_at")

    def test_a_download_entry_can_be_deleted_explicitly(self) -> None:
        from app.models.download import DownloadEntry, DownloadPlatform

        client = self.create_test_client()
        store = client.app.state.download_store
        settings = client.app.state.settings

        download_dir = settings.output_dir / "posts" / "20260812-101500-abc123"
        download_dir.mkdir(parents=True)
        (download_dir / "video.mp4").write_bytes(b"x" * 10)
        store.save_entry(
            DownloadEntry(
                id="20260812-101500-abc123",
                platform=DownloadPlatform.tiktok_post,
                output_dir=str(download_dir),
                files=[str(download_dir / "video.mp4")],
            )
        )

        response = client.delete("/downloads/20260812-101500-abc123")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(download_dir.exists(), "deleting an entry removes its files")
        self.assertIsNone(store.get_entry("20260812-101500-abc123"))
        self.assertEqual(client.delete("/downloads/20260812-101500-abc123").status_code, 404)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_app_reliability -v -k stamps`
Expected: FAIL — the file is gone and `fetched_at` does not exist.

- [ ] **Step 3: Add `fetched_at` to the recording models**

In `app/models/recording.py`, add to `RecordingJob` after `finished_at`:

```python
    fetched_at: Optional[datetime] = None
```

Add the same field to `RecordingJobResponse` after `finished_at`, and in `from_job` add:

```python
            fetched_at=job.fetched_at,
```

- [ ] **Step 4: Replace deletion with a stamp in FileService**

In `app/services/file_service.py`, replace `cleanup_download_artifacts` with:

```python
    def mark_downloaded(self, job_id: str) -> None:
        """Record that the user has been given this recording.

        Deleting here is what used to happen, and it meant an interrupted save
        destroyed the only copy. The sweep removes it once the grace period is
        up; until then a retry works.
        """
        self.job_store.update_job(
            job_id,
            lambda current: current.model_copy(update={"fetched_at": utc_now()}),
        )
        logger.info("Recording marked as downloaded", extra={"job_id": job_id})
```

- [ ] **Step 5: Point the recordings route at it**

In `app/api/recordings.py`, in `download_recording`, change the background task:

```python
        background=BackgroundTask(file_service.mark_downloaded, job_id),
```

- [ ] **Step 6: Stamp on the TikTok post routes and add DELETE**

In `app/api/downloads.py`, add imports:

```python
import shutil

from starlette.background import BackgroundTask
```

Add the background task to `download_file`'s `FileResponse`:

```python
    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type="application/octet-stream",
        background=BackgroundTask(request.app.state.download_store.mark_fetched, download_id),
    )
```

Add the delete route at the end of the router:

```python
@router.delete("/{download_id}")
def delete_download(request: Request, download_id: str) -> dict[str, bool]:
    """Remove a download and its files now, rather than waiting for the sweep."""
    store = request.app.state.download_store
    entry = store.get_entry(download_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="download not found")
    shutil.rmtree(Path(entry.output_dir), ignore_errors=True)
    store.delete_entry(download_id)
    return {"deleted": True}
```

- [ ] **Step 7: Do the same for Instagram**

In `app/instagram/api/downloads.py`, add `import shutil`, replace the two cleanup background tasks with stamps, and add the delete route:

```python
        background=BackgroundTask(request.app.state.download_store.mark_fetched, download_id),
```

for `download_file`, and for `download_all` (the zip route) keep deleting only the temporary archive, which is not user media:

```python
        background=BackgroundTask(_remove_archive_and_stamp, request, download_id, archive_path),
```

with the helper defined below the routes:

```python
def _remove_archive_and_stamp(request: Request, download_id: str, archive_path: Path) -> None:
    """The zip is a temp artifact and always goes; the media it was built from
    is stamped like any other fetch and swept later."""
    archive_path.unlink(missing_ok=True)
    request.app.state.download_store.mark_fetched(download_id)
```

Add the delete route at the end of the Instagram router:

```python
@router.delete("/{download_id}")
def delete_download(request: Request, download_id: str) -> dict[str, bool]:
    """Remove a download and its files now, rather than waiting for the sweep."""
    store = request.app.state.download_store
    entry = store.get_entry(download_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="download not found")
    shutil.rmtree(Path(entry.output_dir), ignore_errors=True)
    store.delete_entry(download_id)
    return {"deleted": True}
```

- [ ] **Step 8: Run the tests**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS, including both new tests.

- [ ] **Step 9: Commit**

```bash
git add app/api/downloads.py app/instagram/api/downloads.py app/api/recordings.py app/services/file_service.py app/models/recording.py tests/test_app_reliability.py
git commit -m "downloads: serving a file marks it fetched instead of destroying it"
```

---

### Task 5: Rewrite the sweep around fetched and orphaned

**Files:**
- Modify: `app/services/cleanup_service.py`
- Modify: `app/main.py`
- Test: `tests/test_cleanup_service.py`
- Delete: the cleanup tests currently inside `tests/test_app_reliability.py` (`CleanupSweepTests`), moved into the new file

**Interfaces:**
- Consumes: `RetentionPolicy` (Task 1), `DownloadStore` (Task 2), `JobStore`, `Settings`.
- Produces: `CleanupService(settings, job_store, download_store, policy, start=True)` with `sweep() -> dict[str, int]` returning keys `expired_downloads`, `expired_recordings`, `orphans_removed`, `logs_removed`; and `diagnostics() -> dict[str, object]` including `policy` (the window values) so the health endpoint shows what is in force.

- [ ] **Step 1: Write the failing test**

Create `tests/test_cleanup_service.py`:

```python
from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

from app.models.download import DownloadEntry, DownloadPlatform
from app.models.recording import RecordingJob, RecordingStatus, utc_now
from app.services.cleanup_service import CleanupService
from app.services.config import Settings
from app.services.download_store import DownloadStore
from app.services.job_store import JobStore
from app.services.retention import RetentionPolicy


class CleanupSweepTests(unittest.TestCase):
    def build(self, root: Path):
        for key, value in {
            "OUTPUT_DIR": str(root / "output"),
            "LOGS_DIR": str(root / "logs"),
            "JOBS_FILE": str(root / "data" / "jobs.json"),
            "WATCH_JOBS_FILE": str(root / "data" / "watch_jobs.json"),
            "DOWNLOADS_FILE": str(root / "data" / "downloads.json"),
        }.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        settings = Settings()
        settings.ensure_directories()
        policy = RetentionPolicy.from_settings(settings)
        job_store = JobStore(settings.jobs_file)
        download_store = DownloadStore(settings.downloads_file)
        service = CleanupService(settings, job_store, download_store, policy, start=False)
        return settings, job_store, download_store, service

    @staticmethod
    def age(path: Path, hours: float) -> None:
        old = time.time() - hours * 3600
        os.utime(path, (old, old))

    def test_an_unfetched_recording_is_never_swept(self) -> None:
        """The rule that yesterday's data loss was missing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, job_store, _, service = self.build(root)
            recording = settings.output_dir / "TK_someone.mp4"
            recording.write_bytes(b"x" * 10)
            self.age(recording, 24 * 30)
            job_store.save_job(
                RecordingJob(username="someone", status=RecordingStatus.finished, file_path=str(recording))
            )

            result = service.sweep()

            self.assertTrue(recording.exists())
            self.assertEqual(result["expired_recordings"], 0)

    def test_a_fetched_recording_goes_once_the_grace_period_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, job_store, _, service = self.build(root)
            recording = settings.output_dir / "TK_someone.mp4"
            recording.write_bytes(b"x" * 10)
            job = RecordingJob(
                username="someone",
                status=RecordingStatus.finished,
                file_path=str(recording),
                fetched_at=utc_now() - timedelta(hours=25),
            )
            job_store.save_job(job)

            result = service.sweep()

            self.assertFalse(recording.exists())
            self.assertIsNone(job_store.get_job(job.id))
            self.assertEqual(result["expired_recordings"], 1)

    def test_a_recording_fetched_a_minute_ago_stays(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, job_store, _, service = self.build(root)
            recording = settings.output_dir / "TK_someone.mp4"
            recording.write_bytes(b"x" * 10)
            job_store.save_job(
                RecordingJob(
                    username="someone",
                    status=RecordingStatus.finished,
                    file_path=str(recording),
                    fetched_at=utc_now() - timedelta(minutes=1),
                )
            )

            service.sweep()

            self.assertTrue(recording.exists())

    def test_a_fetched_download_folder_goes_and_an_unfetched_one_stays(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, _, download_store, service = self.build(root)

            def add(entry_id: str, fetched_at) -> Path:
                folder = settings.output_dir / "posts" / entry_id
                folder.mkdir(parents=True)
                (folder / "video.mp4").write_bytes(b"x" * 10)
                download_store.save_entry(
                    DownloadEntry(
                        id=entry_id,
                        platform=DownloadPlatform.tiktok_post,
                        output_dir=str(folder),
                        files=[str(folder / "video.mp4")],
                        fetched_at=fetched_at,
                    )
                )
                return folder

            fetched = add("fetched", utc_now() - timedelta(hours=25))
            never = add("never", None)

            result = service.sweep()

            self.assertFalse(fetched.exists())
            self.assertTrue(never.exists())
            self.assertEqual(result["expired_downloads"], 1)

    def test_orphans_are_swept_and_referenced_files_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, job_store, _, service = self.build(root)
            orphan = settings.output_dir / "TK_crashed_run_flv.mp4"
            orphan.write_bytes(b"x" * 10)
            self.age(orphan, 48)
            claimed = settings.output_dir / "TK_claimed.mp4"
            claimed.write_bytes(b"x" * 10)
            self.age(claimed, 48)
            job_store.save_job(
                RecordingJob(username="someone", status=RecordingStatus.finished, file_path=str(claimed))
            )

            result = service.sweep()

            self.assertFalse(orphan.exists())
            self.assertTrue(claimed.exists())
            self.assertEqual(result["orphans_removed"], 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_cleanup_service -v`
Expected: FAIL — `CleanupService` does not yet take a download store or a policy, so construction raises `TypeError` (the old signature binds `download_store` to `interval_seconds` and then gets `start` twice).

- [ ] **Step 3: Add the settings entry for the test env var**

`DOWNLOADS_FILE` already works via Task 3's `downloads_file` field — no change needed. Confirm by running:

Run: `.venv/bin/python -c "import os; os.environ['DOWNLOADS_FILE']='/tmp/x.json'; from app.services.config import Settings; print(Settings().downloads_file)"`
Expected: `/tmp/x.json`

- [ ] **Step 4: Rewrite the service**

Replace the whole body of `app/services/cleanup_service.py`:

```python
"""Periodic sweep of runtime output.

A timer may only delete something the user has already been given. An earlier
version of this file deleted by age alone and destroyed a finished 3000-second
recording three hours after it completed, before its owner downloaded it.

So there are exactly three ways media leaves disk: it was fetched and the grace
period expired, the user deleted it, or nothing references it at all. An item
that was never fetched is never swept, whatever the configured windows say.
"""

from __future__ import annotations

import logging
import shutil
import threading
from pathlib import Path

from app.services.config import Settings
from app.services.download_store import DownloadStore
from app.services.job_store import JobStore
from app.services.retention import RetentionPolicy


logger = logging.getLogger(__name__)


class CleanupService:
    def __init__(
        self,
        settings: Settings,
        job_store: JobStore,
        download_store: DownloadStore,
        policy: RetentionPolicy,
        start: bool = True,
    ) -> None:
        self.settings = settings
        self.job_store = job_store
        self.download_store = download_store
        self.policy = policy
        self._stop_event = threading.Event()
        self._last_result: dict[str, int] = {}
        self._sweep_count = 0
        self._thread: threading.Thread | None = None
        if start:
            self._thread = threading.Thread(target=self._run_loop, daemon=True)
            self._thread.start()

    def diagnostics(self) -> dict[str, object]:
        return {
            "thread_alive": bool(self._thread and self._thread.is_alive()),
            "sweep_count": self._sweep_count,
            "last_sweep": dict(self._last_result),
            "policy": {
                "fetched_hours": self.policy.fetched_hours,
                "orphan_hours": self.policy.orphan_hours,
                "log_hours": self.policy.log_hours,
                "interval_seconds": self.policy.interval_seconds,
            },
        }

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sweep()
            except Exception:
                logger.exception("Cleanup sweep failed")
            self._stop_event.wait(self.policy.interval_seconds)

    def sweep(self) -> dict[str, int]:
        result = {
            "expired_downloads": self._sweep_expired_downloads(),
            "expired_recordings": self._sweep_expired_recordings(),
            "orphans_removed": self._sweep_orphans(),
            "logs_removed": self._sweep_logs(),
        }
        self._sweep_count += 1
        self._last_result = result
        if any(result.values()):
            logger.info("Cleanup sweep removed expired media", extra=result)
        return result

    def _sweep_expired_downloads(self) -> int:
        removed = 0
        for entry in self.download_store.list_entries():
            if not self.policy.is_expired(entry.fetched_at, self.policy.fetched_hours):
                continue
            shutil.rmtree(Path(entry.output_dir), ignore_errors=True)
            self.download_store.delete_entry(entry.id)
            removed += 1
        return removed

    def _sweep_expired_recordings(self) -> int:
        removed = 0
        for job in self.job_store.list_jobs():
            if not self.policy.is_expired(job.fetched_at, self.policy.fetched_hours):
                continue
            if job.file_path:
                Path(job.file_path).unlink(missing_ok=True)
            self.job_store.delete_job(job.id)
            removed += 1
        return removed

    def _sweep_orphans(self) -> int:
        """Files and folders no record references. Everything else is someone's."""
        claimed = self._claimed_paths()
        removed = 0

        for path in self.settings.output_dir.glob("*"):
            if not path.is_file() or str(path.resolve()) in claimed:
                continue
            if self.policy.is_older_than(path.stat().st_mtime, self.policy.orphan_hours):
                path.unlink(missing_ok=True)
                removed += 1

        for parent_name in ("posts", "instagram"):
            parent = self.settings.output_dir / parent_name
            if not parent.is_dir():
                continue
            for child in parent.iterdir():
                if not child.is_dir() or str(child.resolve()) in claimed:
                    continue
                if self.policy.is_older_than(self._newest_mtime(child), self.policy.orphan_hours):
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
        return removed

    def _claimed_paths(self) -> set[str]:
        """Every path a job or download entry refers to.

        On a read failure this returns everything on disk, so a broken store
        can never turn into a deletion spree.
        """
        claimed: set[str] = set()
        try:
            for job in self.job_store.list_jobs():
                if job.file_path:
                    claimed.add(str(Path(job.file_path).resolve()))
            for entry in self.download_store.list_entries():
                claimed.add(str(Path(entry.output_dir).resolve()))
                claimed.update(str(Path(path).resolve()) for path in entry.files)
        except Exception:
            logger.exception("Could not read a store; treating everything as claimed")
            return {str(path.resolve()) for path in self.settings.output_dir.rglob("*")}
        return claimed

    def _sweep_logs(self) -> int:
        try:
            live_job_ids = {job.id for job in self.job_store.list_jobs()}
        except Exception:
            return 0

        removed = 0
        for path in self.settings.logs_dir.glob("*.log"):
            job_id = path.name.split(".", 1)[0]
            if job_id in live_job_ids:
                continue
            if self.policy.is_older_than(path.stat().st_mtime, self.policy.log_hours):
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _newest_mtime(self, directory: Path) -> float:
        """Age a folder by its freshest file, so an active download survives."""
        newest = directory.stat().st_mtime
        for path in directory.rglob("*"):
            if path.is_file():
                newest = max(newest, path.stat().st_mtime)
        return newest
```

- [ ] **Step 5: Update the wiring**

In `app/main.py`, add the import:

```python
from app.services.retention import RetentionPolicy
```

and replace the construction:

```python
    retention_policy = RetentionPolicy.from_settings(settings)
    cleanup_service = CleanupService(settings, job_store, download_store, retention_policy)
```

- [ ] **Step 6: Move the old sweep tests out**

Delete the `CleanupSweepTests` class from `tests/test_app_reliability.py` — `tests/test_cleanup_service.py` now covers that ground, including the same regression.

- [ ] **Step 7: Run the tests**

Run: `.venv/bin/python -m unittest discover -s tests -v`
Expected: PASS, all tests.

- [ ] **Step 8: Commit**

```bash
git add app/services/cleanup_service.py app/main.py tests/test_cleanup_service.py tests/test_app_reliability.py
git commit -m "cleanup: sweep only what was fetched or orphaned, never what was not"
```

---

### Task 6: Storage reporting

**Files:**
- Create: `app/services/storage_report.py`
- Modify: `app/main.py`
- Test: `tests/test_storage_report.py`

**Interfaces:**
- Consumes: `RetentionPolicy.storage_soft_limit_bytes` (Task 1).
- Produces: `storage_report(output_dir: Path, soft_limit_bytes: int) -> dict[str, object]` with keys `used_bytes`, `free_bytes`, `soft_limit_bytes`, `over_soft_limit` (bool).

- [ ] **Step 1: Write the failing test**

Create `tests/test_storage_report.py`:

```python
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.storage_report import storage_report


class StorageReportTests(unittest.TestCase):
    def test_counts_bytes_under_the_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "posts").mkdir()
            (root / "posts" / "video.mp4").write_bytes(b"x" * 2048)
            (root / "loose.mp4").write_bytes(b"x" * 1024)

            report = storage_report(root, soft_limit_bytes=10_000)

            self.assertEqual(report["used_bytes"], 3072)
            self.assertFalse(report["over_soft_limit"])
            self.assertGreater(report["free_bytes"], 0)

    def test_flags_when_usage_passes_the_soft_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "big.mp4").write_bytes(b"x" * 4096)

            report = storage_report(root, soft_limit_bytes=1024)

            self.assertTrue(report["over_soft_limit"])

    def test_missing_directory_reports_zero_rather_than_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = storage_report(Path(temp_dir) / "gone", soft_limit_bytes=1024)

            self.assertEqual(report["used_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest tests.test_storage_report -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'app.services.storage_report'`

- [ ] **Step 3: Write it**

Create `app/services/storage_report.py`:

```python
"""What the output directory is costing, for the health endpoint and the UI.

Reporting only. Nothing here deletes: disk pressure is surfaced so the owner
can decide, because the alternative — a sweep that frees space on its own —
is how media gets destroyed without being seen.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def storage_report(output_dir: Path, soft_limit_bytes: int) -> dict[str, object]:
    used = 0
    if output_dir.is_dir():
        for path in output_dir.rglob("*"):
            if path.is_file():
                try:
                    used += path.stat().st_size
                except OSError:  # vanished mid-walk
                    continue

    try:
        free = shutil.disk_usage(output_dir if output_dir.is_dir() else output_dir.parent).free
    except OSError:
        free = 0

    return {
        "used_bytes": used,
        "free_bytes": free,
        "soft_limit_bytes": soft_limit_bytes,
        "over_soft_limit": used > soft_limit_bytes,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m unittest tests.test_storage_report -v`
Expected: PASS, 3 tests

- [ ] **Step 5: Surface it in the health endpoint**

In `app/main.py`, add the import:

```python
from app.services.storage_report import storage_report
```

and add a key to the `health_details` payload, directly after `"stores"`:

```python
            "storage": storage_report(settings.output_dir, retention_policy.storage_soft_limit_bytes),
```

`redact_details` needs no change — the report contains no paths.

- [ ] **Step 6: Run the tests**

Run: `.venv/bin/python -m unittest discover -s tests`
Expected: OK

- [ ] **Step 7: Commit**

```bash
git add app/services/storage_report.py tests/test_storage_report.py app/main.py
git commit -m "health: report what output/ is using and whether it passed the soft limit"
```

---

### Task 7: Show retention in the UI

**Files:**
- Modify: `app/static/js/record-page.js`
- Modify: `app/static/js/download-page.js`
- Modify: `app/static/js/instagram-download-page.js`
- Modify: `app/templates/record.html`
- Modify: `app/templates/base.html`

**Interfaces:**
- Consumes: `fetched_at` on `RecordingJobResponse` (Task 4); `DELETE /downloads/{id}` and `DELETE /instagram/downloads/{id}` (Task 4).
- Produces: no new interfaces.

- [ ] **Step 1: Show when a saved recording will be removed**

In `app/static/js/record-page.js`, add this helper next to `formatBytes`:

```js
  function retentionNote(job) {
    if (!job.fetched_at) return "";
    const removesAt = new Date(new Date(job.fetched_at).getTime() + 24 * 3600 * 1000);
    const hoursLeft = Math.max(0, Math.round((removesAt - Date.now()) / 3600000));
    return `Saved — removed in ~${hoursLeft}h`;
  }
```

and render it inside the job card's `<dl class="job-stats">`, after the File size row:

```js
            ${job.fetched_at ? `<div class="wide"><dt>Retention</dt><dd>${escapeHtml(retentionNote(job))}</dd></div>` : ""}
```

- [ ] **Step 2: Bump the cache-busting version**

In `app/templates/record.html`, change `record-page.js?v=3` to `record-page.js?v=4`. Browsers cache these aggressively; without the bump you will be testing the old file.

- [ ] **Step 3: Verify by hand**

Run the app locally:

```bash
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`, and with a finished recording present, download it. Expected: the card stays in the list and gains a "Saved — removed in ~24h" row, where previously it disappeared.

- [ ] **Step 4: Make the footer figure real**

In `app/templates/base.html`, replace the hardcoded footer item:

```html
        <span class="footer-item">Storage limit: ~20 GB</span>
```

with a placeholder the page fills in:

```html
        <span class="footer-item" id="storage-note">Storage: …</span>
```

and in `app/static/js/app-common.js`, add:

```js
async function refreshStorageNote() {
  const el = document.getElementById("storage-note");
  if (!el) return;
  try {
    const response = await fetch(appPath("/health/details"));
    if (!response.ok) return;
    const { storage } = await response.json();
    if (!storage) return;
    const gb = (bytes) => (bytes / 1024 ** 3).toFixed(1);
    el.textContent = `Storage: ${gb(storage.used_bytes)} GB used, ${gb(storage.free_bytes)} GB free`;
    el.className = storage.over_soft_limit ? "footer-item warn" : "footer-item";
  } catch {
    // The footer is decoration; a failed poll must never surface as an error.
  }
}

document.addEventListener("DOMContentLoaded", refreshStorageNote);
```

- [ ] **Step 5: Add the warning style**

In `app/static/css/app.css`, add next to the other footer rules:

```css
.footer-item.warn { color: var(--danger, #b3261e); font-weight: 600; }
```

- [ ] **Step 6: Verify by hand**

Reload `http://127.0.0.1:8000`. Expected: the footer reads e.g. "Storage: 0.0 GB used, 13.0 GB free" instead of the static claim.

- [ ] **Step 7: Add a Delete button to the download pages**

The spec calls for reclaiming space without waiting for the sweep. Recordings
already have a Delete button; downloads never did, because they used to delete
themselves. In `app/static/js/download-page.js`, inside `renderResult`, add a
button to the job card footer, after the file list:

```js
      <footer class="job-actions">
        <button class="btn btn-danger" data-action="delete-download" data-id="${escapeHtml(download.download_id)}">Delete from server</button>
      </footer>
```

and wire it up at the end of the page's init function:

```js
  resultContainer.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-action='delete-download']");
    if (!button) return;
    try {
      const response = await fetch(appPath(`/downloads/${button.dataset.id}`), { method: "DELETE" });
      if (!response.ok) throw new Error(await readApiError(response, "Could not delete the download."));
      resultContainer.innerHTML = emptyState();
      setNotice(notice, "Deleted from the server.");
    } catch (error) {
      setNotice(notice, error.message, "error");
    }
  });
```

Apply the identical change to `app/static/js/instagram-download-page.js`, with
the endpoint path `/instagram/downloads/${button.dataset.id}`.

- [ ] **Step 8: Run the full suite**

Run: `.venv/bin/python -m unittest discover -s tests`
Expected: OK

- [ ] **Step 9: Commit**

```bash
git add app/static/js/record-page.js app/static/js/download-page.js app/static/js/instagram-download-page.js app/static/js/app-common.js app/static/css/app.css app/templates/record.html app/templates/base.html
git commit -m "ui: show when saved media will be removed, and real storage use"
```

---

## Deployment

Not part of any task. When the whole plan is done and the suite is green:

```bash
cd /opt/ttl-downloader && git pull && sudo systemctl restart ttl-downloader
```

Then confirm the policy in force and that nothing was destroyed on startup:

```bash
curl -s https://<host>/tiktok/health/details | python3 -m json.tool
```

Expected: `services.cleanup.policy` shows the configured windows, `services.cleanup.last_sweep` shows zeroes on a clean server, and `storage` reports real numbers.
