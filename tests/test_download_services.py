from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.download_store import DownloadStore
from app.services.post_download_service import (
    PostDownloadResult,
    PostDownloadService,
    friendly_download_error,
    should_try_fallback,
)


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


class DownloadErrorTests(unittest.TestCase):
    """yt-dlp's raw errors tell the user to file a bug on yt-dlp's tracker,
    which is never the right advice for someone who pasted a dead link."""

    def test_an_unavailable_post_reads_like_an_explanation(self) -> None:
        raw = (
            "ERROR: [TikTok] 7430349171061804293: Unexpected response from webpage request; "
            "please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out "
            "the appropriate issue template."
        )

        message = friendly_download_error(raw)

        self.assertNotIn("github.com", message)
        self.assertIn("no longer available", message)

    def test_an_unknown_error_is_passed_through_unchanged(self) -> None:
        raw = "ERROR: something nobody has seen before"

        self.assertEqual(friendly_download_error(raw), raw)

    def test_any_yt_dlp_failure_is_worth_a_fallback_attempt(self) -> None:
        """The fallback used to run only for 'Unsupported URL' and 403. tikwm
        can serve posts yt-dlp chokes on for other reasons too, and trying it
        costs one request."""
        for raw in (
            "ERROR: Unsupported URL: https://www.tiktok.com/@x/photo/1",
            "ERROR: HTTP Error 403: Forbidden",
            "ERROR: [TikTok] 123: Unexpected response from webpage request",
            "ERROR: Unable to extract webpage video data",
        ):
            self.assertTrue(should_try_fallback(raw), raw)

    def test_a_validation_failure_is_not_worth_a_fallback(self) -> None:
        self.assertFalse(should_try_fallback("download URL must be a TikTok URL"))


if __name__ == "__main__":
    unittest.main()
