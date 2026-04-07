from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from pathlib import Path

from app.models.recording import RecordingCreateRequest, RecordingJob, RecordingStatus, utc_now
from app.services.config import Settings
from app.services.file_service import FileService
from app.services.job_store import JobStore


logger = logging.getLogger(__name__)


class RecorderService:
    def __init__(self, settings: Settings, job_store: JobStore, file_service: FileService) -> None:
        self.settings = settings
        self.job_store = job_store
        self.file_service = file_service
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()

    def create_job(self, payload: RecordingCreateRequest) -> RecordingJob:
        if self.job_store.has_active_job():
            active = self.job_store.get_active_job()
            raise RuntimeError(f"another recording job is already active: {active.id if active else 'unknown'}")

        job = RecordingJob(
            username=payload.username,
            url=str(payload.url) if payload.url else None,
            duration=payload.duration,
            status=RecordingStatus.queued,
        )
        self.job_store.save_job(job)

        thread = threading.Thread(target=self._run_job, args=(job.id,), daemon=True)
        thread.start()
        return job

    def stop_job(self, job_id: str) -> RecordingJob:
        job = self.job_store.get_job(job_id)
        if not job:
            raise KeyError("job not found")
        if job.status != RecordingStatus.running:
            raise RuntimeError("only running jobs can be stopped")
        if not job.pid:
            raise RuntimeError("job pid is not available")

        self.job_store.update_job(
            job_id,
            lambda current: current.model_copy(update={"status": RecordingStatus.stopped}),
        )
        self._terminate_process(job.pid)
        self._wait_for_thread_cleanup(job_id)

        updated = self.job_store.get_job(job_id)
        if not updated:
            raise KeyError("job disappeared after stop")
        return updated

    def delete_job(self, job_id: str) -> bool:
        job = self.job_store.get_job(job_id)
        if not job:
            return False
        if job.status == RecordingStatus.running and job.pid:
            self._terminate_process(job.pid)
        if job.file_path:
            Path(job.file_path).unlink(missing_ok=True)
        return self.job_store.delete_job(job_id)

    def _run_job(self, job_id: str) -> None:
        job = self.job_store.get_job(job_id)
        if not job:
            return

        before = self.file_service.snapshot_output()
        command = self._build_command(job)
        logger.info("Starting recorder command", extra={"job_id": job_id, "command": command})

        try:
            process = self._start_process(command)
        except Exception as exc:
            self.job_store.update_job(
                job_id,
                lambda current: current.model_copy(
                    update={
                        "status": RecordingStatus.failed,
                        "error": str(exc),
                        "finished_at": utc_now(),
                    }
                ),
            )
            logger.exception("Failed to start recorder process", extra={"job_id": job_id})
            return

        with self._lock:
            self._processes[job_id] = process

        self.job_store.update_job(job_id, lambda current: current.model_copy(
            update={
                "status": RecordingStatus.running,
                "pid": process.pid,
                "started_at": utc_now(),
                "error": None,
            }
        ))

        stdout, stderr = process.communicate()
        after = self.file_service.snapshot_output()
        detected_file = self.file_service.detect_output_file(before, after)
        return_code = process.returncode

        with self._lock:
            self._processes.pop(job_id, None)

        if return_code == 0:
            status = RecordingStatus.finished
            error = None
        else:
            job_after = self.job_store.get_job(job_id)
            status = RecordingStatus.stopped if job_after and job_after.status == RecordingStatus.stopped else RecordingStatus.failed
            error = stderr.strip() or stdout.strip() or f"recorder exited with code {return_code}"

        if status == RecordingStatus.stopped and detected_file is None:
            status = RecordingStatus.failed
            error = error or "recording stopped before output file was created"

        file_path = str(detected_file) if detected_file else None
        if return_code == 0 and not file_path:
            status = RecordingStatus.failed
            error = "recorder finished but no output file was detected"

        self.job_store.update_job(
            job_id,
            lambda current: current.model_copy(
                update={
                    "status": status,
                    "file_path": file_path,
                    "finished_at": utc_now(),
                    "error": error,
                    "pid": None,
                }
            ),
        )

    def _build_command(self, job: RecordingJob) -> list[str]:
        command = [
            self.settings.python_bin,
            str(self.settings.recorder_entrypoint),
            "-output",
            str(self.settings.output_dir),
        ]
        if job.username:
            command.extend(["-user", job.username])
        if job.url:
            command.extend(["-url", job.url])
        if self.settings.recorder_mode:
            command.extend(["-mode", self.settings.recorder_mode])
        if job.duration:
            command.extend(["-duration", str(job.duration)])
        if self.settings.recorder_proxy:
            command.extend(["-proxy", self.settings.recorder_proxy])
        if self.settings.recorder_bitrate:
            command.extend(["-bitrate", self.settings.recorder_bitrate])
        if self.settings.skip_update_check:
            command.append("-no-update-check")
        return command

    def _start_process(self, command: list[str]) -> subprocess.Popen[str]:
        kwargs: dict[str, object] = {
            "args": command,
            "cwd": str(self.settings.recorder_dir),
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "text": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["preexec_fn"] = os.setsid
        return subprocess.Popen(**kwargs)

    def _terminate_process(self, pid: int) -> None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            return

    def _wait_for_thread_cleanup(self, job_id: str, attempts: int = 30, delay_seconds: float = 0.5) -> None:
        for _ in range(attempts):
            current = self.job_store.get_job(job_id)
            if current and current.status in {RecordingStatus.stopped, RecordingStatus.failed, RecordingStatus.finished}:
                return
            threading.Event().wait(delay_seconds)
