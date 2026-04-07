from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Callable

from app.models.recording import RecordingJob, RecordingStatus


class JobStore:
    def __init__(self, jobs_file: Path) -> None:
        self.jobs_file = jobs_file
        self._lock = threading.RLock()
        if not self.jobs_file.exists():
            self.jobs_file.write_text("[]\n", encoding="utf-8")

    def list_jobs(self) -> list[RecordingJob]:
        with self._lock:
            return sorted(self._read_jobs(), key=lambda item: item.created_at, reverse=True)

    def get_job(self, job_id: str) -> RecordingJob | None:
        with self._lock:
            jobs = self._read_jobs()
            return next((job for job in jobs if job.id == job_id), None)

    def save_job(self, job: RecordingJob) -> RecordingJob:
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

    def update_job(self, job_id: str, updater: Callable[[RecordingJob], RecordingJob]) -> RecordingJob | None:
        with self._lock:
            jobs = self._read_jobs()
            for index, job in enumerate(jobs):
                if job.id == job_id:
                    jobs[index] = updater(job)
                    self._write_jobs(jobs)
                    return jobs[index]
        return None

    def delete_job(self, job_id: str) -> bool:
        with self._lock:
            jobs = self._read_jobs()
            updated = [job for job in jobs if job.id != job_id]
            if len(updated) == len(jobs):
                return False
            self._write_jobs(updated)
            return True

    def has_active_job(self) -> bool:
        active_statuses = {RecordingStatus.queued, RecordingStatus.running}
        return any(job.status in active_statuses for job in self.list_jobs())

    def get_active_job(self) -> RecordingJob | None:
        active_statuses = {RecordingStatus.queued, RecordingStatus.running}
        for job in self.list_jobs():
            if job.status in active_statuses:
                return job
        return None

    def _read_jobs(self) -> list[RecordingJob]:
        raw = self.jobs_file.read_text(encoding="utf-8-sig").strip()
        if not raw:
            return []
        data = json.loads(raw)
        return [RecordingJob.model_validate(item) for item in data]

    def _write_jobs(self, jobs: list[RecordingJob]) -> None:
        payload = [job.model_dump(mode="json") for job in jobs]
        self.jobs_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
