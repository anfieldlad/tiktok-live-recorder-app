from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.download_store import DownloadStore
from app.services.post_download_service import PostDownloadResult, PostDownloadService


class PostDownloadServicePersistenceTests(unittest.TestCase):
    def test_results_are_readable_after_a_restart(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "data" / "downloads.json")
            service = PostDownloadService(root / "output", download_store=store)

            download_dir = service.output_dir / "20260812-101500-abc123"
            download_dir.mkdir(parents=True)
            media = download_dir / "video.mp4"
            media.write_bytes(b"x" * 10)
            service.remember(
                PostDownloadResult(
                    download_id="20260812-101500-abc123",
                    output_dir=download_dir,
                    files=[media],
                )
            )

            restarted = PostDownloadService(
                root / "output",
                download_store=DownloadStore(root / "data" / "downloads.json"),
            )

            result = restarted.get_result("20260812-101500-abc123")
            self.assertIsNotNone(result, "a completed download must survive a restart")
            self.assertEqual(restarted.resolve_file("20260812-101500-abc123", 0), media.resolve())


if __name__ == "__main__":
    unittest.main()
