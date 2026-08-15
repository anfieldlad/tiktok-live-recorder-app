from __future__ import annotations

import os
import tempfile
import time
import unittest
from datetime import timedelta
from pathlib import Path

from app.models.download import DownloadEntry, DownloadPlatform, DownloadStatus
from app.models.recording import RecordingJob, RecordingStatus, utc_now
from app.services.cleanup_service import CleanupService
from app.services.config import Settings
from app.services.download_store import DownloadStore
from app.services.job_store import JobStore
from app.services.retention import RetentionPolicy


class CleanupSweepTests(unittest.TestCase):
    def build(self, root: Path):
        for key, value in {
            "OUTPUT_DIR": str(root / "output"),
            "LOGS_DIR": str(root / "logs"),
            "JOBS_FILE": str(root / "data" / "jobs.json"),
            "WATCH_JOBS_FILE": str(root / "data" / "watch_jobs.json"),
            "DOWNLOADS_FILE": str(root / "data" / "downloads.json"),
        }.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        settings = Settings()
        settings.ensure_directories()
        policy = RetentionPolicy.from_settings(settings)
        job_store = JobStore(settings.jobs_file)
        download_store = DownloadStore(settings.downloads_file)
        service = CleanupService(settings, job_store, download_store, policy, start=False)
        return settings, job_store, download_store, service

    @staticmethod
    def age(path: Path, hours: float) -> None:
        old = time.time() - hours * 3600
        os.utime(path, (old, old))

    def test_an_unfetched_recording_is_never_swept(self) -> None:
        """The rule that yesterday's data loss was missing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, job_store, _, service = self.build(root)
            recording = settings.output_dir / "TK_someone.mp4"
            recording.write_bytes(b"x" * 10)
            self.age(recording, 24 * 30)
            job_store.save_job(
                RecordingJob(username="someone", status=RecordingStatus.finished, file_path=str(recording))
            )

            result = service.sweep()

            self.assertTrue(recording.exists())
            self.assertEqual(result["expired_recordings"], 0)

    def test_a_fetched_recording_goes_once_the_grace_period_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, job_store, _, service = self.build(root)
            recording = settings.output_dir / "TK_someone.mp4"
            recording.write_bytes(b"x" * 10)
            job = RecordingJob(
                username="someone",
                status=RecordingStatus.finished,
                file_path=str(recording),
                fetched_at=utc_now() - timedelta(hours=25),
            )
            job_store.save_job(job)

            result = service.sweep()

            self.assertFalse(recording.exists())
            self.assertIsNone(job_store.get_job(job.id))
            self.assertEqual(result["expired_recordings"], 1)

    def test_a_recording_fetched_a_minute_ago_stays(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, job_store, _, service = self.build(root)
            recording = settings.output_dir / "TK_someone.mp4"
            recording.write_bytes(b"x" * 10)
            job_store.save_job(
                RecordingJob(
                    username="someone",
                    status=RecordingStatus.finished,
                    file_path=str(recording),
                    fetched_at=utc_now() - timedelta(minutes=1),
                )
            )

            service.sweep()

            self.assertTrue(recording.exists())

    def test_a_fetched_download_folder_goes_and_an_unfetched_one_stays(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, _, download_store, service = self.build(root)

            def add(entry_id: str, fetched_at) -> Path:
                folder = settings.output_dir / "posts" / entry_id
                folder.mkdir(parents=True)
                (folder / "video.mp4").write_bytes(b"x" * 10)
                download_store.save_entry(
                    DownloadEntry(
                        id=entry_id,
                        platform=DownloadPlatform.tiktok_post,
                        output_dir=str(folder),
                        files=[str(folder / "video.mp4")],
                        fetched_at=fetched_at,
                    )
                )
                return folder

            fetched = add("fetched", utc_now() - timedelta(hours=25))
            never = add("never", None)

            result = service.sweep()

            self.assertFalse(fetched.exists())
            self.assertTrue(never.exists())
            self.assertEqual(result["expired_downloads"], 1)

    def test_orphans_are_swept_and_referenced_files_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, job_store, _, service = self.build(root)
            orphan = settings.output_dir / "TK_crashed_run_flv.mp4"
            orphan.write_bytes(b"x" * 10)
            self.age(orphan, 48)
            claimed = settings.output_dir / "TK_claimed.mp4"
            claimed.write_bytes(b"x" * 10)
            self.age(claimed, 48)
            job_store.save_job(
                RecordingJob(username="someone", status=RecordingStatus.finished, file_path=str(claimed))
            )

            result = service.sweep()

            self.assertFalse(orphan.exists())
            self.assertTrue(claimed.exists())
            self.assertEqual(result["orphans_removed"], 1)


if __name__ == "__main__":
    unittest.main()


class CleanupToleratesJobsTests(CleanupSweepTests):
    def test_a_queued_entry_has_no_files_and_must_not_break_the_sweep(self) -> None:
        """`Path("").resolve()` is the working directory, not nothing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, download_store, service = self.build(root)
            download_store.save_entry(
                DownloadEntry(
                    id="queued-1",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.queued,
                    url="https://www.tiktok.com/@a/video/1",
                )
            )

            result = service.sweep()

            self.assertEqual(result["expired_downloads"], 0)
            self.assertIsNotNone(download_store.get_entry("queued-1"))

    def test_a_running_download_is_never_swept_as_an_orphan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            settings, _, download_store, service = self.build(root)
            live_dir = settings.output_dir / "posts" / "running-1"
            live_dir.mkdir(parents=True)
            (live_dir / "part.mp4").write_bytes(b"x")
            self.age(live_dir / "part.mp4", 24 * 30)
            self.age(live_dir, 24 * 30)
            download_store.save_entry(
                DownloadEntry(
                    id="running-1",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.running,
                    output_dir=str(live_dir),
                )
            )

            service.sweep()

            self.assertTrue(live_dir.exists(), "work in flight is claimed, however old its files look")

    def test_a_failed_entry_eventually_leaves_the_register(self) -> None:
        """It has no files and no fetched_at, so nothing else would ever remove it."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, download_store, service = self.build(root)
            download_store.save_entry(
                DownloadEntry(
                    id="failed-1",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.failed,
                    error="that post is gone",
                    created_at=utc_now() - timedelta(hours=48),
                    finished_at=utc_now() - timedelta(hours=48),
                )
            )

            result = service.sweep()

            self.assertEqual(result["dead_downloads"], 1)
            self.assertIsNone(download_store.get_entry("failed-1"))

    def test_a_recent_failure_stays_visible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _, _, download_store, service = self.build(root)
            download_store.save_entry(
                DownloadEntry(
                    id="failed-2",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.failed,
                    error="that post is gone",
                    finished_at=utc_now(),
                )
            )

            service.sweep()

            self.assertIsNotNone(download_store.get_entry("failed-2"))
