from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.models.download import (
    DownloadEntry,
    DownloadPlatform,
    DownloadStatus,
    new_download_id,
)
from app.services.download_store import DownloadStore


def make_entry(store_dir: Path, entry_id: str = "20260812-101500-abc123") -> DownloadEntry:
    return DownloadEntry(
        id=entry_id,
        platform=DownloadPlatform.tiktok_post,
        output_dir=str(store_dir / "posts" / entry_id),
        files=[str(store_dir / "posts" / entry_id / "video.mp4")],
    )


class DownloadStoreTests(unittest.TestCase):
    def test_entries_survive_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "downloads.json"
            DownloadStore(path).save_entry(make_entry(root))

            reopened = DownloadStore(path)

            entries = reopened.list_entries()
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].id, "20260812-101500-abc123")
            self.assertIsNone(entries[0].fetched_at)

    def test_mark_fetched_stamps_once_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "downloads.json")
            store.save_entry(make_entry(root))

            first = store.mark_fetched("20260812-101500-abc123")
            second = store.mark_fetched("20260812-101500-abc123")

            self.assertIsNotNone(first.fetched_at)
            self.assertIsNotNone(second.fetched_at)
            self.assertGreaterEqual(second.fetched_at, first.fetched_at)

    def test_mark_fetched_on_unknown_id_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = DownloadStore(Path(temp_dir) / "downloads.json")

            self.assertIsNone(store.mark_fetched("nope"))

    def test_delete_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "downloads.json")
            store.save_entry(make_entry(root))

            self.assertTrue(store.delete_entry("20260812-101500-abc123"))
            self.assertFalse(store.delete_entry("20260812-101500-abc123"))
            self.assertEqual(store.list_entries(), [])

    def test_corrupt_file_is_backed_up_and_reset(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "downloads.json"
            path.write_text("{broken", encoding="utf-8")

            store = DownloadStore(path)

            self.assertEqual(store.list_entries(), [])
            self.assertEqual(path.read_text(encoding="utf-8"), "[]\n")
            self.assertEqual(len(list(root.glob("downloads.corrupt-*.json"))), 1)
            self.assertEqual(store.diagnostics()["recovery_count"], 1)


class DownloadEntryLifecycleTests(unittest.TestCase):
    def test_a_record_written_before_the_job_model_still_loads(self) -> None:
        """Old rows have no status; they are completed downloads, not queued ones."""
        with tempfile.TemporaryDirectory() as temp_dir:
            downloads_file = Path(temp_dir) / "downloads.json"
            downloads_file.write_text(
                json.dumps([
                    {
                        "id": "20260812-101500-abc123",
                        "platform": "tiktok_post",
                        "output_dir": "/tmp/out",
                        "files": ["/tmp/out/video.mp4"],
                        "created_at": "2026-08-12T10:15:00+00:00",
                        "fetched_at": None,
                    }
                ]),
                encoding="utf-8",
            )

            entries = DownloadStore(downloads_file).list_entries()

            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].status, DownloadStatus.finished)
            self.assertIsNone(entries[0].url)

    def test_a_queued_entry_needs_no_output_dir(self) -> None:
        entry = DownloadEntry(
            id="20260815-090000-aaaaaa",
            platform=DownloadPlatform.tiktok_post,
            status=DownloadStatus.queued,
            url="https://www.tiktok.com/@someone/video/123",
        )

        self.assertEqual(entry.output_dir, "")
        self.assertEqual(entry.files, [])

    def test_new_download_id_is_unique_within_the_same_second(self) -> None:
        self.assertNotEqual(new_download_id(), new_download_id())


class DownloadStoreJobTests(unittest.TestCase):
    def store(self, temp_dir: str) -> DownloadStore:
        return DownloadStore(Path(temp_dir) / "downloads.json")

    def test_update_entry_applies_the_updater(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            store.save_entry(
                DownloadEntry(id="a", platform=DownloadPlatform.tiktok_post, status=DownloadStatus.queued)
            )

            updated = store.update_entry(
                "a", lambda current: current.model_copy(update={"status": DownloadStatus.running})
            )

            self.assertIsNotNone(updated)
            self.assertEqual(store.get_entry("a").status, DownloadStatus.running)

    def test_update_entry_on_an_unknown_id_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            self.assertIsNone(store.update_entry("nope", lambda current: current))

    def test_a_restart_fails_jobs_that_were_still_in_flight(self) -> None:
        """Their subprocess died with the process. Left alone they spin forever."""
        with tempfile.TemporaryDirectory() as temp_dir:
            store = self.store(temp_dir)
            store.save_entry(DownloadEntry(id="q", platform=DownloadPlatform.tiktok_post, status=DownloadStatus.queued))
            store.save_entry(DownloadEntry(id="r", platform=DownloadPlatform.instagram, status=DownloadStatus.running))
            store.save_entry(
                DownloadEntry(id="f", platform=DownloadPlatform.tiktok_post, status=DownloadStatus.finished)
            )

            failed = store.fail_orphaned_jobs()

            self.assertEqual(failed, 2)
            self.assertEqual(store.get_entry("q").status, DownloadStatus.failed)
            self.assertEqual(store.get_entry("r").status, DownloadStatus.failed)
            self.assertEqual(store.get_entry("f").status, DownloadStatus.finished)
            self.assertIn("restart", store.get_entry("r").error)


if __name__ == "__main__":
    unittest.main()
