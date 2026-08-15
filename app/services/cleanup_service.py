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

from app.models.download import DownloadStatus
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
            "dead_downloads": self._sweep_dead_downloads(),
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
            # A queued or failed job has no directory. `Path("").resolve()` is
            # the working directory, not nothing, so this guard is not cosmetic.
            if not entry.output_dir:
                continue
            if not self.policy.is_expired(entry.fetched_at, self.policy.fetched_hours):
                continue
            shutil.rmtree(Path(entry.output_dir), ignore_errors=True)
            self.download_store.delete_entry(entry.id)
            removed += 1
        return removed

    def _sweep_dead_downloads(self) -> int:
        """Failed jobs left nothing on disk, so no other rule reaches them.

        Without this the register accumulates every dead link ever pasted: a
        failed entry has neither files nor `fetched_at`, and both of the other
        sweeps key off exactly those. The orphan window is the right clock — it
        already means "how long we keep something nobody is waiting for".
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
                if entry.output_dir:
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
