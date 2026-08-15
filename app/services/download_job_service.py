"""Downloads as background jobs, mirroring RecorderService.

Nothing here serializes work that used to run in parallel — the handlers were
always sync `def` and always ran concurrently in the threadpool. What this adds
is a *bound* on that concurrency (a 1.9 GB, 2-core box will not survive an
unbounded number of yt-dlp processes) and a persisted lifecycle, so a download
in progress is something the UI can see rather than something hidden inside a
request that has not returned yet.

The fetchers are untouched. They are called from a worker thread with an id the
caller already knows, and that is the whole of the change on their side.
"""

from __future__ import annotations

import logging
import queue
import threading

from app.models.download import (
    DownloadEntry,
    DownloadPlatform,
    DownloadStatus,
    new_download_id,
)
from app.models.recording import utc_now
from app.services.download_store import DownloadStore
from app.services.redaction import redact_sensitive


logger = logging.getLogger(__name__)

_STOP = "__stop__"


class DownloadJobService:
    def __init__(
        self,
        download_store: DownloadStore,
        post_download_service: object,
        instagram_download_service: object,
        max_workers: int = 2,
        start: bool = True,
    ) -> None:
        self.download_store = download_store
        self._services = {
            DownloadPlatform.tiktok_post: post_download_service,
            DownloadPlatform.instagram: instagram_download_service,
        }
        self._max_workers = max(1, int(max_workers))
        self._queue: queue.Queue[str] = queue.Queue()
        self._finished: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._running: set[str] = set()
        self._threads: list[threading.Thread] = []
        if start:
            self.start()

    def start(self) -> None:
        for index in range(self._max_workers):
            thread = threading.Thread(
                target=self._worker_loop, name=f"download-worker-{index}", daemon=True
            )
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 10.0) -> None:
        """Drain the pool and wait for the workers to leave.

        In production the daemon threads die with the process and this is never
        called. It exists for tests, and it *joins* on purpose: a stop that
        returned while a worker was still writing files would make every test
        that owns a temp directory racy.
        """
        for _ in self._threads:
            self._queue.put(_STOP)
        for thread in self._threads:
            thread.join(timeout)
        self._threads.clear()

    def diagnostics(self) -> dict[str, object]:
        with self._lock:
            running = sorted(self._running)
        return {
            "max_workers": self._max_workers,
            "running_count": len(running),
            "running": running,
            "queued_count": self._queue.qsize(),
        }

    def submit(self, url: str, platform: DownloadPlatform) -> DownloadEntry:
        """Persist a queued job and return at once.

        Validation happens here, on the request thread, so a link that is not a
        TikTok or Instagram URL is still a 422 and never becomes a job.
        """
        service = self._services[platform]
        normalized_url = service.validate_url(url)

        entry = DownloadEntry(
            id=new_download_id(),
            platform=platform,
            status=DownloadStatus.queued,
            url=normalized_url,
        )
        self.download_store.save_entry(entry)
        with self._lock:
            self._finished[entry.id] = threading.Event()
        self._queue.put(entry.id)
        return entry

    def wait(self, download_id: str, timeout: float | None = None) -> DownloadEntry | None:
        """Block until the job reaches a terminal state.

        This is what the synchronous door is built on: the current Android app
        posts and waits for the payload, and must keep working exactly as it
        does today.
        """
        with self._lock:
            event = self._finished.get(download_id)
        if event is not None:
            event.wait(timeout)
        return self.download_store.get_entry(download_id)

    def _worker_loop(self) -> None:
        while True:
            download_id = self._queue.get()
            try:
                if download_id == _STOP:
                    return
                self._run(download_id)
            except Exception:  # a worker thread must never die
                logger.exception("Unhandled download job failure", extra={"download_id": download_id})
            finally:
                self._queue.task_done()
                if download_id != _STOP:
                    with self._lock:
                        event = self._finished.get(download_id)
                    if event is not None:
                        event.set()

    def _run(self, download_id: str) -> None:
        entry = self.download_store.get_entry(download_id)
        if entry is None:
            return

        self.download_store.update_entry(
            download_id,
            lambda current: current.model_copy(
                update={"status": DownloadStatus.running, "started_at": utc_now(), "error": None}
            ),
        )
        with self._lock:
            self._running.add(download_id)

        try:
            service = self._services[entry.platform]
            service.download(entry.url, download_id=download_id)
        except Exception as exc:
            # ValueError (a bad URL) cannot reach here — submit() validated it —
            # so anything caught is a fetch failure and belongs on the card.
            logger.info("Download job failed", extra={"download_id": download_id})
            self.download_store.update_entry(
                download_id,
                lambda current: current.model_copy(
                    update={
                        "status": DownloadStatus.failed,
                        "error": redact_sensitive(str(exc)) or "the download failed",
                        "finished_at": utc_now(),
                    }
                ),
            )
        else:
            self.download_store.update_entry(
                download_id,
                lambda current: current.model_copy(
                    update={
                        "status": DownloadStatus.finished,
                        "error": None,
                        "finished_at": utc_now(),
                    }
                ),
            )
        finally:
            with self._lock:
                self._running.discard(download_id)
