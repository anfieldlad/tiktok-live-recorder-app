from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from app.models.download import DownloadPlatform, DownloadStatus
from app.services.download_job_service import DownloadJobService
from app.services.download_store import DownloadStore


class FakeDownloadService:
    """Stands in for a fetcher. Each call blocks until the test releases it."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.started = threading.Semaphore(0)
        self.release = threading.Event()
        self.failing_urls: set[str] = set()
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def validate_url(self, url: str) -> str:
        if "bad" in url:
            raise ValueError("download URL must be a TikTok URL")
        return url.strip()

    def download(self, url: str, download_id: str | None = None):
        with self._lock:
            self.calls.append((url, download_id or ""))
        self.started.release()
        self.release.wait(timeout=5)
        if url in self.failing_urls:
            raise RuntimeError("that post is no longer available")
        directory = self.output_dir / (download_id or "x")
        directory.mkdir(parents=True, exist_ok=True)
        return None


class DownloadJobServiceTests(unittest.TestCase):
    def build(self, max_workers: int = 2):
        # Cleanups run last-registered-first, so the pool is joined before the
        # directory it is writing into is removed.
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        root = Path(temp_dir.name)
        store = DownloadStore(root / "data" / "downloads.json")
        tiktok = FakeDownloadService(root / "output" / "posts")
        instagram = FakeDownloadService(root / "output" / "instagram")
        service = DownloadJobService(store, tiktok, instagram, max_workers=max_workers)
        self.addCleanup(service.stop)
        return store, tiktok, instagram, service

    def test_submit_returns_before_the_work_completes(self) -> None:
        store, tiktok, _, service = self.build()

        entry = service.submit("https://www.tiktok.com/@a/video/1", DownloadPlatform.tiktok_post)

        self.assertIn(entry.status, {DownloadStatus.queued, DownloadStatus.running})
        self.assertEqual(store.get_entry(entry.id).url, "https://www.tiktok.com/@a/video/1")
        self.assertTrue(tiktok.started.acquire(timeout=5), "a worker should have picked the job up")
        tiktok.release.set()

    def test_a_rejected_url_never_becomes_a_job(self) -> None:
        store, _, _, service = self.build()

        with self.assertRaises(ValueError):
            service.submit("https://example.com/bad", DownloadPlatform.tiktok_post)

        self.assertEqual(store.list_entries(), [])

    def test_a_third_submission_waits_for_a_slot(self) -> None:
        store, tiktok, _, service = self.build(max_workers=2)

        first = service.submit("https://www.tiktok.com/@a/video/1", DownloadPlatform.tiktok_post)
        second = service.submit("https://www.tiktok.com/@a/video/2", DownloadPlatform.tiktok_post)
        third = service.submit("https://www.tiktok.com/@a/video/3", DownloadPlatform.tiktok_post)

        self.assertTrue(tiktok.started.acquire(timeout=5))
        self.assertTrue(tiktok.started.acquire(timeout=5))
        self.assertEqual(store.get_entry(third.id).status, DownloadStatus.queued)
        self.assertEqual(len(tiktok.calls), 2, "only two may run at once")

        tiktok.release.set()
        self.assertIsNotNone(service.wait(first.id, timeout=5))
        self.assertIsNotNone(service.wait(second.id, timeout=5))
        self.assertEqual(service.wait(third.id, timeout=5).status, DownloadStatus.finished)

    def test_a_failing_job_does_not_stall_the_queue(self) -> None:
        store, tiktok, _, service = self.build(max_workers=1)
        tiktok.failing_urls.add("https://www.tiktok.com/@a/video/dead")
        tiktok.release.set()

        bad = service.submit("https://www.tiktok.com/@a/video/dead", DownloadPlatform.tiktok_post)
        good = service.submit("https://www.tiktok.com/@a/video/2", DownloadPlatform.tiktok_post)

        failed = service.wait(bad.id, timeout=5)
        self.assertEqual(failed.status, DownloadStatus.failed)
        self.assertIn("no longer available", failed.error)
        self.assertEqual(service.wait(good.id, timeout=5).status, DownloadStatus.finished)

    def test_the_worker_gets_the_id_that_was_persisted(self) -> None:
        _, tiktok, _, service = self.build(max_workers=1)
        tiktok.release.set()

        entry = service.submit("https://www.tiktok.com/@a/video/1", DownloadPlatform.tiktok_post)
        service.wait(entry.id, timeout=5)

        self.assertEqual(tiktok.calls[0][1], entry.id)

    def test_instagram_jobs_go_to_the_instagram_fetcher(self) -> None:
        _, tiktok, instagram, service = self.build(max_workers=1)
        instagram.release.set()

        entry = service.submit("https://www.instagram.com/p/abc/", DownloadPlatform.instagram)
        service.wait(entry.id, timeout=5)

        self.assertEqual(len(instagram.calls), 1)
        self.assertEqual(tiktok.calls, [])


if __name__ == "__main__":
    unittest.main()
