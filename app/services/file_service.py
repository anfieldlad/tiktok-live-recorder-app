from __future__ import annotations

import logging
import subprocess
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
        return self.choose_recording_file(after - before)

    def choose_recording_file(self, files: set[Path] | list[Path]) -> Path | None:
        candidates = [path for path in files if path.is_file()]
        if not candidates:
            return None

        def sort_key(path: Path) -> tuple[int, float]:
            # Prefer finalized outputs over temporary recorder artifacts like *_flv.mp4.
            is_temp = 1 if "_flv" in path.stem.lower() else 0
            return (is_temp, -path.stat().st_mtime)

        return min(candidates, key=sort_key)

    def cleanup_temporary_variants(self, recording_file: Path) -> list[str]:
        deleted: list[str] = []
        temp_stem = f"{recording_file.stem}_flv"
        for candidate in self.output_dir.glob(f"{temp_stem}*"):
            if not candidate.is_file():
                continue
            candidate.unlink(missing_ok=True)
            deleted.append(str(candidate))
        return deleted

    def remux_temporary_recording(self, temp_file: Path, ffmpeg_bin: str) -> Path | None:
        if not temp_file.exists() or "_flv" not in temp_file.stem.lower():
            return None

        final_name = temp_file.name.replace("_flv", "", 1)
        final_path = temp_file.with_name(final_name)
        if final_path.exists():
            final_path.unlink(missing_ok=True)

        command = [
            ffmpeg_bin,
            "-y",
            "-i",
            str(temp_file),
            "-c",
            "copy",
            str(final_path),
        ]
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode != 0 or not final_path.exists():
            logger.warning(
                "Failed to remux temporary recorder output",
                extra={"temp_file": str(temp_file), "error": result.stderr.strip()},
            )
            final_path.unlink(missing_ok=True)
            return None

        temp_file.unlink(missing_ok=True)
        return final_path

    def resolve_job_file(self, job: RecordingJob) -> Path:
        if not job.file_path:
            raise FileNotFoundError("recording file path is not set")
        file_path = Path(job.file_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError("recording file does not exist")
        return file_path

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
