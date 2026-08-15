from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.models.download import DownloadEntry, DownloadStatus
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
