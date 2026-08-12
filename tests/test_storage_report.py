from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.storage_report import storage_report


class StorageReportTests(unittest.TestCase):
    def test_counts_bytes_under_the_output_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "posts").mkdir()
            (root / "posts" / "video.mp4").write_bytes(b"x" * 2048)
            (root / "loose.mp4").write_bytes(b"x" * 1024)

            report = storage_report(root, soft_limit_bytes=10_000)

            self.assertEqual(report["used_bytes"], 3072)
            self.assertFalse(report["over_soft_limit"])
            self.assertGreater(report["free_bytes"], 0)

    def test_flags_when_usage_passes_the_soft_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "big.mp4").write_bytes(b"x" * 4096)

            report = storage_report(root, soft_limit_bytes=1024)

            self.assertTrue(report["over_soft_limit"])

    def test_missing_directory_reports_zero_rather_than_raising(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            report = storage_report(Path(temp_dir) / "gone", soft_limit_bytes=1024)

            self.assertEqual(report["used_bytes"], 0)


if __name__ == "__main__":
    unittest.main()
