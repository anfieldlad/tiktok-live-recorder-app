from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from app.models.recording import WatchJob, WatchStatus


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
        raw = self.watch_jobs_file.read_text(encoding="utf-8-sig").strip()
        if not raw:
            return []
        data = json.loads(raw)
        return [WatchJob.model_validate(item) for item in data]

    def _write_jobs(self, jobs: list[WatchJob]) -> None:
        payload = [job.model_dump(mode="json") for job in jobs]
        self.watch_jobs_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
