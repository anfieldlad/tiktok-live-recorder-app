from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.instagram.services.instagram_cookie_service import InstagramCookieService
from app.instagram.services.instagram_download_service import InstagramDownloadService
from app.models.download import DownloadEntry, DownloadPlatform, DownloadStatus
from app.services.cookie_service import CookieService
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

    def test_the_other_wording_yt_dlp_uses_for_the_same_dead_post(self) -> None:
        """Matching exact phrases is whack-a-mole: the same dead post produced
        "Unexpected response from webpage request" one hour and "Unable to
        extract universal data for rehydration" the next."""
        raw = (
            "ERROR: [TikTok] 7430349171061804293: Unable to extract universal data for rehydration; "
            "please report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the "
            "appropriate issue template. Confirm you are on the latest version using  yt-dlp -U"
        )

        message = friendly_download_error(raw)

        self.assertNotIn("github.com", message)
        self.assertNotIn("yt-dlp -U", message)
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


class DownloadIdOwnershipTests(unittest.TestCase):
    """A job needs its id before the fetch begins, so the caller can poll it."""

    def test_remember_keeps_the_lifecycle_the_job_service_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "data" / "downloads.json")
            service = PostDownloadService(root / "output", download_store=store)
            store.save_entry(
                DownloadEntry(
                    id="20260815-101500-abc123",
                    platform=DownloadPlatform.tiktok_post,
                    status=DownloadStatus.running,
                    url="https://www.tiktok.com/@someone/video/123",
                )
            )

            download_dir = service.output_dir / "20260815-101500-abc123"
            download_dir.mkdir(parents=True)
            media = download_dir / "video.mp4"
            media.write_bytes(b"x" * 10)
            service.remember(
                PostDownloadResult(
                    download_id="20260815-101500-abc123", output_dir=download_dir, files=[media]
                )
            )

            entry = store.get_entry("20260815-101500-abc123")
            self.assertEqual(entry.status, DownloadStatus.running, "the worker owns the status, not remember()")
            self.assertEqual(entry.url, "https://www.tiktok.com/@someone/video/123")
            self.assertEqual(entry.files, [str(media)])

    def test_remember_still_creates_an_entry_when_nothing_pre_allocated_one(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            store = DownloadStore(root / "data" / "downloads.json")
            service = PostDownloadService(root / "output", download_store=store)

            download_dir = service.output_dir / "20260815-101500-def456"
            download_dir.mkdir(parents=True)
            media = download_dir / "video.mp4"
            media.write_bytes(b"x" * 10)
            service.remember(
                PostDownloadResult(
                    download_id="20260815-101500-def456", output_dir=download_dir, files=[media]
                )
            )

            self.assertEqual(store.get_entry("20260815-101500-def456").status, DownloadStatus.finished)


class CookielessFetchTests(unittest.TestCase):
    """Without the key a fetch still runs — it just runs as nobody."""

    def build(self, temp_dir: str) -> PostDownloadService:
        root = Path(temp_dir)
        cookies_file = root / "cookies.json"
        cookies_file.write_text('{"sessionid": "secret-value"}', encoding="utf-8")
        return PostDownloadService(root / "output", cookie_service=CookieService(cookies_file))

    def test_a_session_request_writes_a_cookie_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self.build(temp_dir)

            cookie_file = service._write_cookie_file(use_session=True)

            self.assertIsNotNone(cookie_file)
            self.assertIn("secret-value", cookie_file.read_text(encoding="utf-8"))
            cookie_file.unlink(missing_ok=True)

    def test_an_anonymous_request_writes_no_cookie_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            service = self.build(temp_dir)

            self.assertIsNone(service._write_cookie_file(use_session=False))

    def test_instagram_does_the_same(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            cookies_file = root / "instagram_cookies.json"
            cookies_file.write_text('{"sessionid": "secret-value"}', encoding="utf-8")
            service = InstagramDownloadService(
                root / "output", cookie_service=InstagramCookieService(cookies_file)
            )

            self.assertIsNone(service._write_cookie_file(use_session=False))

    def test_the_room_lookup_can_be_told_to_skip_the_cookie_jar(self) -> None:
        """The vendor recorder reads a fixed cookies.json; this script is ours,
        so it is the one place the decision can be honoured."""
        from app.services.live_status_service import _ROOM_LOOKUP_SCRIPT

        self.assertIn('payload.get("use_session", True)', _ROOM_LOOKUP_SCRIPT)
