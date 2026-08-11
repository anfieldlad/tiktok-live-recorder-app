from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path

from app.models.recording import (
    RecordingCreateRequest,
    RecordingJob,
    RecordingProgress,
    RecordingStatus,
    utc_now,
)
from app.services.config import Settings
from app.services.file_service import FileService
from app.services.job_store import JobStore
from app.services.redaction import redact_sensitive


logger = logging.getLogger(__name__)


class RecorderService:
    def __init__(self, settings: Settings, job_store: JobStore, file_service: FileService) -> None:
        self.settings = settings
        self.job_store = job_store
        self.file_service = file_service
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.RLock()

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            active_processes = {
                job_id: process.pid
                for job_id, process in self._processes.items()
                if process.poll() is None
            }
        return {
            "active_process_count": len(active_processes),
            "active_processes": active_processes,
        }

    def create_job(self, payload: RecordingCreateRequest) -> RecordingJob:
        if self.job_store.has_active_job():
            active = self.job_store.get_active_job()
            raise RuntimeError(f"another recording job is already active: {active.id if active else 'unknown'}")

        job = RecordingJob(
            username=payload.username,
            url=str(payload.url) if payload.url else None,
            duration=payload.duration,
            status=RecordingStatus.queued,
            progress=RecordingProgress.preparing,
            progress_message="Preparing the recorder.",
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
            lambda current: current.model_copy(
                update={
                    "status": RecordingStatus.stopped,
                    "progress": RecordingProgress.stopped,
                    "progress_message": "Stopping the recording...",
                }
            ),
        )
        with self._lock:
            process = self._processes.get(job_id)
        self._terminate_process(
            process=process,
            pid=job.pid,
            grace_seconds=self.settings.process_stop_grace_seconds,
        )
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
            with self._lock:
                process = self._processes.get(job_id)
            self._terminate_process(process=process, pid=job.pid)
        if job.file_path:
            Path(job.file_path).unlink(missing_ok=True)
        return self.job_store.delete_job(job_id)

    def _run_job(self, job_id: str) -> None:
        try:
            self._run_job_inner(job_id)
        except Exception as exc:
            logger.exception("Unhandled recorder job failure", extra={"job_id": job_id})
            with self._lock:
                process = self._processes.pop(job_id, None)
            if process is not None and process.poll() is None:
                self._terminate_process(process=process, pid=process.pid)
            self.job_store.update_job(
                job_id,
                lambda current: current.model_copy(
                    update={
                        "status": RecordingStatus.failed,
                        "progress": RecordingProgress.failed,
                        "progress_message": "The recording ended with an unexpected error.",
                        "error": redact_sensitive(str(exc)),
                        "finished_at": utc_now(),
                        "pid": None,
                    }
                ),
            )

    def _run_job_inner(self, job_id: str) -> None:
        job = self.job_store.get_job(job_id)
        if not job:
            return

        before = self.file_service.snapshot_output()
        command = self._build_command(job)
        stdout_log_path = self.settings.logs_dir / f"{job_id}.stdout.log"
        stderr_log_path = self.settings.logs_dir / f"{job_id}.stderr.log"
        logger.info("Starting recorder command", extra={"job_id": job_id, "command": command})

        stdout_handle = stdout_log_path.open("w", encoding="utf-8")
        stderr_handle = stderr_log_path.open("w", encoding="utf-8")
        try:
            process = self._start_process(command, stdout_handle, stderr_handle)
        except Exception as exc:
            stdout_handle.close()
            stderr_handle.close()
            self.job_store.update_job(
                job_id,
                lambda current: current.model_copy(
                    update={
                        "status": RecordingStatus.failed,
                        "progress": RecordingProgress.failed,
                        "progress_message": "The recorder could not be started.",
                        "error": redact_sensitive(str(exc)),
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
                "progress": RecordingProgress.recording,
                "progress_message": "Recording is in progress.",
                "pid": process.pid,
                "started_at": utc_now(),
                "error": None,
            }
        ))

        timed_out = False
        deadline = time.monotonic() + job.duration if job.duration else None
        while True:
            try:
                return_code = process.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() >= deadline:
                    timed_out = True
                    self._terminate_process(
                        process=process,
                        pid=process.pid,
                        grace_seconds=self.settings.process_stop_grace_seconds,
                    )
                    try:
                        return_code = process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        return_code = process.poll() if process.poll() is not None else -9
                    break

        stdout_handle.close()
        stderr_handle.close()
        # The full, unredacted output stays in the per-job log files on disk.
        stdout = redact_sensitive(self._read_log_tail(stdout_log_path))
        stderr = redact_sensitive(self._read_log_tail(stderr_log_path))
        self.job_store.update_job(
            job_id,
            lambda current: current.model_copy(
                update={
                    "progress": RecordingProgress.finalizing,
                    "progress_message": "Finalizing the recording...",
                }
            ),
        )
        after = self.file_service.snapshot_output()
        detected_file = self.file_service.detect_output_file(before, after)
        temp_artifact_detected = bool(detected_file and "_flv" in detected_file.stem.lower())
        if temp_artifact_detected and detected_file is not None:
            remuxed_file = self.file_service.remux_temporary_recording(detected_file, self.settings.ffmpeg_bin)
            if remuxed_file is not None:
                detected_file = remuxed_file
                temp_artifact_detected = False
        if process.returncode is not None:
            return_code = process.returncode

        with self._lock:
            self._processes.pop(job_id, None)

        if timed_out:
            status = RecordingStatus.finished
            progress = RecordingProgress.ready
            progress_message = "Recording reached the requested duration and is ready to download."
            error = None
        elif return_code == 0:
            status = RecordingStatus.finished
            progress = RecordingProgress.ready
            progress_message = "Recording finished and ready to download."
            error = None
        else:
            job_after = self.job_store.get_job(job_id)
            status = RecordingStatus.stopped if job_after and job_after.status == RecordingStatus.stopped else RecordingStatus.failed
            progress = RecordingProgress.stopped if status == RecordingStatus.stopped else RecordingProgress.failed
            progress_message = "Recording stopped." if status == RecordingStatus.stopped else "The recording ended with an error."
            error = stderr.strip() or stdout.strip() or f"recorder exited with code {return_code}"

        if status == RecordingStatus.stopped and detected_file is not None and not temp_artifact_detected:
            status = RecordingStatus.finished
            progress = RecordingProgress.ready
            progress_message = "Recording stopped and saved."
            error = None

        if status == RecordingStatus.stopped and detected_file is None:
            status = RecordingStatus.failed
            progress = RecordingProgress.failed
            progress_message = "The recording stopped before a file was created."
            error = error or "recording stopped before output file was created"

        file_path = str(detected_file) if detected_file else None
        if detected_file and temp_artifact_detected:
            was_manual_stop = status == RecordingStatus.stopped
            file_path = None
            status = RecordingStatus.failed
            progress = RecordingProgress.failed
            if was_manual_stop:
                progress_message = "The recording stopped before the video could be finalized."
                error = error or "recorder stopped before finalizing the video"
            else:
                progress_message = "The recording ended before the video could be finalized."
                error = error or "recorder left only a temporary output file"
        if (return_code == 0 or timed_out) and not file_path:
            status = RecordingStatus.failed
            progress = RecordingProgress.failed
            progress_message = "The recording finished, but no file was created."
            error = "recorder finished but no output file was detected"

        if file_path:
            self.file_service.cleanup_temporary_variants(Path(file_path))

        self.job_store.update_job(
            job_id,
            lambda current: current.model_copy(
                update={
                    "status": status,
                    "progress": progress,
                    "progress_message": progress_message,
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

    def _start_process(
        self,
        command: list[str],
        stdout_handle: object,
        stderr_handle: object,
    ) -> subprocess.Popen[str]:
        kwargs: dict[str, object] = {
            "args": command,
            "cwd": str(self.settings.recorder_dir),
            "stdout": stdout_handle,
            "stderr": stderr_handle,
            "text": True,
        }
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            # Same effect as preexec_fn=os.setsid, but preexec_fn is unsafe in a
            # threaded process and jobs are started from worker threads.
            kwargs["start_new_session"] = True
        return subprocess.Popen(**kwargs)

    def _read_log_tail(self, path: Path, max_chars: int = 4000) -> str:
        if not path.exists():
            return ""
        text = path.read_text(encoding="utf-8", errors="ignore")
        if len(text) <= max_chars:
            return text.strip()
        return text[-max_chars:].strip()

    def _terminate_process(
        self,
        *,
        process: subprocess.Popen[str] | None = None,
        pid: int | None = None,
        grace_seconds: int | None = None,
    ) -> None:
        target_pid = pid or (process.pid if process else None)
        if target_pid is None:
            return
        grace = grace_seconds if grace_seconds is not None else self.settings.process_stop_grace_seconds

        if os.name == "nt":
            if process and process.poll() is None:
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                    process.wait(timeout=grace)
                    return
                except (subprocess.TimeoutExpired, OSError, ValueError):
                    pass
            subprocess.run(
                ["taskkill", "/PID", str(target_pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return

        try:
            pgid = os.getpgid(target_pid)
        except ProcessLookupError:
            return

        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return

        end_time = time.monotonic() + max(grace, 0)
        while process and process.poll() is None and time.monotonic() < end_time:
            time.sleep(0.2)

        if process and process.poll() is not None:
            return

        try:
            os.killpg(pgid, signal.SIGKILL)
        except ProcessLookupError:
            return

    def _wait_for_thread_cleanup(self, job_id: str, attempts: int = 30, delay_seconds: float = 0.5) -> None:
        for _ in range(attempts):
            current = self.job_store.get_job(job_id)
            if current and current.status in {RecordingStatus.stopped, RecordingStatus.failed, RecordingStatus.finished}:
                return
            threading.Event().wait(delay_seconds)
