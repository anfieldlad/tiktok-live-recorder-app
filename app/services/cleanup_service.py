"""Periodic sweep of runtime output.

`FileService.cleanup_old_files` existed but nothing ever called it, so
`output/posts/` and `output/instagram/` grew forever while finished recordings
cleaned themselves up on download. Download ids also live only in memory, so
every restart orphans whatever was left on disk — those folders are unreachable
through the API and pure dead weight.

Age alone is not a safe test, which the first version of this learned the hard
way: it deleted a finished 3000-second recording three hours after it completed,
before its owner downloaded it. A recording belongs to its job and lives until
the job is downloaded or deleted, so anything a job still points at is skipped
here no matter how old it is. What remains sweepable is genuinely unreachable:
orphaned files from crashed runs, per-download folders whose ids died with the
process that held them in memory, and logs for jobs that no longer exist.
"""

from __future__ import annotations

import logging
import shutil
import threading
from datetime import timedelta
from pathlib import Path

from app.models.recording import utc_now
from app.services.config import Settings
from app.services.job_store import JobStore


logger = logging.getLogger(__name__)


class CleanupService:
    def __init__(
        self,
        settings: Settings,
        job_store: JobStore,
        interval_seconds: int | None = None,
        start: bool = True,
    ) -> None:
        self.settings = settings
        self.job_store = job_store
        self.interval_seconds = interval_seconds or settings.cleanup_interval_minutes * 60
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
            "interval_seconds": self.interval_seconds,
            "sweep_count": self._sweep_count,
            "last_sweep": dict(self._last_result),
        }

    def stop(self) -> None:
        self._stop_event.set()

    def _run_loop(self) -> None:
        # Sweep once at startup: most of what accumulates predates this process.
        while not self._stop_event.is_set():
            try:
                self.sweep()
            except Exception:
                logger.exception("Cleanup sweep failed")
            self._stop_event.wait(self.interval_seconds)

    def sweep(self) -> dict[str, int]:
        result = {
            "recordings_removed": self._sweep_recordings(),
            "download_dirs_removed": self._sweep_download_dirs(),
            "logs_removed": self._sweep_logs(),
        }
        self._sweep_count += 1
        self._last_result = result
        if any(result.values()):
            logger.info("Cleanup sweep removed old runtime files", extra=result)
        return result

    def _cutoff(self, hours: int) -> float:
        return (utc_now() - timedelta(hours=hours)).timestamp()

    def _sweep_recordings(self) -> int:
        """Loose recording files that no job claims.

        A file a job still points at is never swept, at any age. The app's
        contract is that a recording survives until it is downloaded (the
        download deletes it) or the job is deleted — sweeping by age alone
        destroyed a finished 3000s recording before its owner fetched it.
        Only orphans are removed: leftovers from crashed or interrupted runs
        that no job references and nothing can serve.
        """
        cutoff = self._cutoff(self.settings.cleanup_max_age_hours)
        claimed = self._claimed_files()
        removed = 0
        for path in self.settings.output_dir.glob("*"):
            if not path.is_file():
                continue
            if str(path.resolve()) in claimed:
                continue
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _claimed_files(self) -> set[str]:
        """Absolute paths any known job refers to, whatever its status."""
        try:
            jobs = self.job_store.list_jobs()
        except Exception:
            # If the store cannot be read, assume everything is claimed rather
            # than risk deleting a recording somebody is waiting for.
            return {str(path.resolve()) for path in self.settings.output_dir.glob("*")}
        return {
            str(Path(job.file_path).resolve())
            for job in jobs
            if job.file_path
        }

    def _sweep_download_dirs(self) -> int:
        """Per-download folders under output/posts and output/instagram.

        Their ids only exist in an in-memory dict, so anything older than the
        window is unreachable through the API no matter what.
        """
        cutoff = self._cutoff(self.settings.cleanup_max_age_hours)
        removed = 0
        for parent_name in ("posts", "instagram"):
            parent = self.settings.output_dir / parent_name
            if not parent.is_dir():
                continue
            for child in parent.iterdir():
                if not child.is_dir():
                    continue
                if self._newest_mtime(child) < cutoff:
                    shutil.rmtree(child, ignore_errors=True)
                    removed += 1
        return removed

    def _sweep_logs(self) -> int:
        """Recorder logs whose job is gone. Logs for live jobs are kept."""
        cutoff = self._cutoff(self.settings.log_max_age_hours)
        try:
            live_job_ids = {job.id for job in self.job_store.list_jobs()}
        except Exception:
            return 0

        removed = 0
        for path in self.settings.logs_dir.glob("*.log"):
            job_id = path.name.split(".", 1)[0]
            if job_id in live_job_ids:
                continue
            if path.stat().st_mtime < cutoff:
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
