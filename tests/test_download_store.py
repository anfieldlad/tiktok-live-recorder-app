from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.models.download import DownloadEntry, DownloadPlatform
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


if __name__ == "__main__":
    unittest.main()
