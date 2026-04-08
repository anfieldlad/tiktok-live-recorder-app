from __future__ import annotations

import logging
import threading
import time

from app.models.recording import (
    RecordingCreateRequest,
    RecordingStatus,
    WatchCreateRequest,
    WatchJob,
    WatchStatus,
    utc_now,
)
from app.services.job_store import JobStore
from app.services.live_status_service import LiveStatusService
from app.services.recorder_service import RecorderService
from app.services.watch_store import WatchStore


logger = logging.getLogger(__name__)


class WatchService:
    def __init__(
        self,
        watch_store: WatchStore,
        job_store: JobStore,
        live_status_service: LiveStatusService,
        recorder_service: RecorderService,
        poll_interval_seconds: int = 45,
    ) -> None:
        self.watch_store = watch_store
        self.job_store = job_store
        self.live_status_service = live_status_service
        self.recorder_service = recorder_service
        self.poll_interval_seconds = poll_interval_seconds
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def create_watch(self, payload: WatchCreateRequest) -> WatchJob:
        watch = WatchJob(
            username=payload.username,
            url=str(payload.url) if payload.url else None,
            duration=payload.duration,
            status=WatchStatus.watching,
            last_message="Watching this account and waiting for a live to start.",
        )
        return self.watch_store.save_job(watch)

    def stop_watch(self, watch_id: str) -> WatchJob:
        updated = self.watch_store.update_job(
            watch_id,
            lambda current: current.model_copy(
                update={
                    "status": WatchStatus.stopped,
                    "finished_at": utc_now(),
                    "last_message": "Watch mode stopped.",
                }
            ),
        )
        if not updated:
            raise KeyError("watch job not found")
        return updated

    def delete_watch(self, watch_id: str) -> bool:
        return self.watch_store.delete_job(watch_id)

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception:
                logger.exception("Watch loop tick failed")
            self._stop_event.wait(self.poll_interval_seconds)

    def _tick(self) -> None:
        active_watch_jobs = self.watch_store.active_jobs()
        for watch in active_watch_jobs:
            try:
                self._process_watch(watch)
            except Exception as exc:
                logger.exception("Watch job processing failed", extra={"watch_id": watch.id})
                self.watch_store.update_job(
                    watch.id,
                    lambda current: current.model_copy(
                        update={
                            "status": WatchStatus.failed,
                            "finished_at": utc_now(),
                            "last_checked_at": utc_now(),
                            "last_message": str(exc) or "Watch mode ended with an unexpected error.",
                        }
                    ),
                )

    def _process_watch(self, watch: WatchJob) -> None:
        current_watch = self.watch_store.get_job(watch.id)
        if not current_watch or current_watch.status not in {WatchStatus.watching, WatchStatus.recording}:
            return
        watch = current_watch

        if watch.status == WatchStatus.recording and watch.linked_recording_job_id:
            linked = self.job_store.get_job(watch.linked_recording_job_id)
            if linked and linked.status in {RecordingStatus.queued, RecordingStatus.running}:
                self.watch_store.update_job(
                    watch.id,
                    lambda current: current.model_copy(
                        update={
                            "last_checked_at": utc_now(),
                            "last_message": "Recording is in progress.",
                        }
                    ),
                )
                return

            if linked and linked.status == RecordingStatus.failed:
                self.watch_store.update_job(
                    watch.id,
                    lambda current: current.model_copy(
                        update={
                            "status": WatchStatus.failed,
                            "finished_at": utc_now(),
                            "last_checked_at": utc_now(),
                            "last_message": linked.error or "The automatic recording failed.",
                        }
                    ),
                )
                return

            if linked and linked.status == RecordingStatus.stopped:
                self.watch_store.update_job(
                    watch.id,
                    lambda current: current.model_copy(
                        update={
                            "status": WatchStatus.stopped,
                            "finished_at": utc_now(),
                            "last_checked_at": utc_now(),
                            "last_message": "The automatic recording was stopped.",
                        }
                    ),
                )
                return

            self.watch_store.update_job(
                watch.id,
                lambda current: current.model_copy(
                    update={
                        "status": WatchStatus.completed,
                        "finished_at": utc_now(),
                        "last_checked_at": utc_now(),
                        "last_message": "Watch completed after the recording finished.",
                    }
                ),
            )
            return

        if self.job_store.has_active_job():
            self.watch_store.update_job(
                watch.id,
                lambda current: current.model_copy(
                    update={
                        "last_checked_at": utc_now(),
                        "last_message": "Waiting for the current recording slot to become available.",
                    }
                ),
            )
            return

        payload = RecordingCreateRequest(username=watch.username, url=watch.url, duration=watch.duration)
        live_status = self.live_status_service.check(payload)
        if not live_status.can_record:
            self.watch_store.update_job(
                watch.id,
                lambda current: current.model_copy(
                    update={
                        "last_checked_at": utc_now(),
                        "last_message": live_status.message,
                    }
                ),
            )
            return

        recording_job = self.recorder_service.create_job(payload)
        self.watch_store.update_job(
            watch.id,
            lambda current: current.model_copy(
                update={
                    "status": WatchStatus.recording,
                    "linked_recording_job_id": recording_job.id,
                    "last_checked_at": utc_now(),
                    "last_message": "Live found. Recording started automatically.",
                }
            ),
        )
