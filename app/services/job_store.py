from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from app.models.recording import RecordingJob, RecordingStatus


logger = logging.getLogger(__name__)


class JobStore:
    def __init__(self, jobs_file: Path) -> None:
        self.jobs_file = jobs_file
        self._lock = threading.RLock()
        self._recovery_count = 0
        self._last_recovery_at: str | None = None
        self._last_recovery_backup_file: str | None = None
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

    def diagnostics(self) -> dict[str, object]:
        return {
            "jobs_file": str(self.jobs_file),
            "recovery_count": self._recovery_count,
            "last_recovery_at": self._last_recovery_at,
            "last_recovery_backup_file": self._last_recovery_backup_file,
        }

    def _read_jobs(self) -> list[RecordingJob]:
        try:
            raw = self.jobs_file.read_text(encoding="utf-8-sig").strip()
        except OSError as exc:
            logger.exception("Failed to read jobs file", extra={"jobs_file": str(self.jobs_file)})
            raise RuntimeError(f"failed to read jobs file: {exc}") from exc
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if not isinstance(data, list):
                raise ValueError("jobs file must contain a JSON array")
            return [RecordingJob.model_validate(item) for item in data]
        except Exception as exc:
            logger.exception("Recovering corrupt jobs file", extra={"jobs_file": str(self.jobs_file)})
            self._recover_corrupt_file(raw)
            return []

    def _write_jobs(self, jobs: list[RecordingJob]) -> None:
        payload = [job.model_dump(mode="json") for job in jobs]
        serialized = json.dumps(payload, indent=2) + "\n"
        temp_file = self.jobs_file.with_name(f"{self.jobs_file.name}.tmp")
        temp_file.write_text(serialized, encoding="utf-8")
        temp_file.replace(self.jobs_file)

    def _recover_corrupt_file(self, raw: str) -> None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup_file = self.jobs_file.with_name(f"{self.jobs_file.stem}.corrupt-{timestamp}{self.jobs_file.suffix}")
        try:
            if raw.strip():
                backup_file.write_text(raw + ("\n" if not raw.endswith("\n") else ""), encoding="utf-8")
        except OSError:
            logger.exception("Failed to back up corrupt jobs file", extra={"jobs_file": str(self.jobs_file)})
        self.jobs_file.write_text("[]\n", encoding="utf-8")
        self._recovery_count += 1
        self._last_recovery_at = datetime.now(timezone.utc).isoformat()
        self._last_recovery_backup_file = str(backup_file)
