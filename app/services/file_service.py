from __future__ import annotations

import logging
from datetime import datetime
from datetime import timedelta
from pathlib import Path

from app.models.recording import RecordingJob, utc_now
from app.services.job_store import JobStore


logger = logging.getLogger(__name__)


class FileService:
    def __init__(self, output_dir: Path, job_store: JobStore) -> None:
        self.output_dir = output_dir
        self.job_store = job_store
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def snapshot_output(self) -> set[Path]:
        return {path.resolve() for path in self.output_dir.glob("*") if path.is_file()}

    def detect_output_file(self, before: set[Path], after: set[Path]) -> Path | None:
        new_files = list(after - before)
        if not new_files:
            return None
        return max(new_files, key=lambda path: path.stat().st_mtime)

    def resolve_job_file(self, job: RecordingJob) -> Path:
        if not job.file_path:
            raise FileNotFoundError("recording file path is not set")
        file_path = Path(job.file_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError("recording file does not exist")
        return file_path

    def cleanup_download_artifacts(self, job_id: str) -> None:
        job = self.job_store.get_job(job_id)
        if not job:
            return
        try:
            if job.file_path:
                file_path = Path(job.file_path)
                if file_path.exists():
                    file_path.unlink()
                    logger.info("Deleted recording file after download", extra={"job_id": job_id})
        finally:
            self.job_store.delete_job(job_id)

    def cleanup_old_files(self, max_age_hours: int) -> list[str]:
        cutoff = utc_now() - timedelta(hours=max_age_hours)
        deleted: list[str] = []
        for file_path in self.output_dir.glob("*"):
            if not file_path.is_file():
                continue
            modified = datetime.fromtimestamp(file_path.stat().st_mtime, tz=utc_now().tzinfo)
            if modified < cutoff:
                file_path.unlink(missing_ok=True)
                deleted.append(str(file_path))
        return deleted
