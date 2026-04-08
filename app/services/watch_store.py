from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.models.recording import WatchJob, WatchStatus


logger = logging.getLogger(__name__)


class WatchStore:
    def __init__(self, watch_jobs_file: Path) -> None:
        self.watch_jobs_file = watch_jobs_file
        self._lock = threading.RLock()
        if not self.watch_jobs_file.exists():
            self.watch_jobs_file.write_text("[]\n", encoding="utf-8")

    def list_jobs(self) -> list[WatchJob]:
        with self._lock:
            return sorted(self._read_jobs(), key=lambda item: item.created_at, reverse=True)

    def get_job(self, watch_id: str) -> WatchJob | None:
        with self._lock:
            jobs = self._read_jobs()
            return next((job for job in jobs if job.id == watch_id), None)

    def save_job(self, job: WatchJob) -> WatchJob:
        with self._lock:
            jobs = self._read_jobs()
            for index, existing in enumerate(jobs):
                if existing.id == job.id:
                    jobs[index] = job
                    break
            else:
                jobs.append(job)
            self._write_jobs(jobs)
        return job

    def update_job(self, watch_id: str, updater: Callable[[WatchJob], WatchJob]) -> WatchJob | None:
        with self._lock:
            jobs = self._read_jobs()
            for index, job in enumerate(jobs):
                if job.id == watch_id:
                    jobs[index] = updater(job)
                    self._write_jobs(jobs)
                    return jobs[index]
        return None

    def delete_job(self, watch_id: str) -> bool:
        with self._lock:
            jobs = self._read_jobs()
            updated = [job for job in jobs if job.id != watch_id]
            if len(updated) == len(jobs):
                return False
            self._write_jobs(updated)
            return True

    def active_jobs(self) -> list[WatchJob]:
        return [job for job in self.list_jobs() if job.status in {WatchStatus.watching, WatchStatus.recording}]

    def _read_jobs(self) -> list[WatchJob]:
        try:
            raw = self.watch_jobs_file.read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            logger.exception("Failed to read watch jobs file", extra={"watch_jobs_file": str(self.watch_jobs_file)})
            raise RuntimeError(f"failed to read watch jobs file: {exc}") from exc
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError("watch jobs file must contain a JSON array")
            return [WatchJob.model_validate(item) for item in data]
        except Exception:
            logger.exception("Recovering corrupt watch jobs file", extra={"watch_jobs_file": str(self.watch_jobs_file)})
            self._recover_corrupt_file(raw)
            return []

    def _write_jobs(self, jobs: list[WatchJob]) -> None:
        payload = [job.model_dump(mode="json") for job in jobs]
        serialized = json.dumps(payload, indent=2) + "\n"
        temp_file = self.watch_jobs_file.with_name(f"{self.watch_jobs_file.name}.tmp")
        temp_file.write_text(serialized, encoding="utf-8")
        temp_file.replace(self.watch_jobs_file)

    def _recover_corrupt_file(self, raw: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_file = self.watch_jobs_file.with_name(
            f"{self.watch_jobs_file.stem}.corrupt-{timestamp}{self.watch_jobs_file.suffix}"
        )
        try:
            if raw.strip():
                backup_file.write_text(raw + ("\n" if not raw.endswith("\n") else ""), encoding="utf-8")
        except OSError:
            logger.exception(
                "Failed to back up corrupt watch jobs file",
                extra={"watch_jobs_file": str(self.watch_jobs_file)},
            )
        self.watch_jobs_file.write_text("[]\n", encoding="utf-8")
