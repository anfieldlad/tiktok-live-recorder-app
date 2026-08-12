from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.cookie_service import CookieService
from app.services.job_store import JobStore
from app.services.redaction import redact_sensitive
from app.services.url_guard import ensure_public_http_url, validate_tiktok_url
from app.services.watch_store import WatchStore


class StoreRecoveryTests(unittest.TestCase):
    def test_job_store_recovers_from_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            jobs_file = Path(temp_dir) / "jobs.json"
            jobs_file.write_text("{broken", encoding="utf-8")

            store = JobStore(jobs_file)

            self.assertEqual(store.list_jobs(), [])
            self.assertEqual(jobs_file.read_text(encoding="utf-8"), "[]\n")
            backups = list(Path(temp_dir).glob("jobs.corrupt-*.json"))
            self.assertEqual(len(backups), 1)

    def test_watch_store_recovers_from_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            watch_jobs_file = Path(temp_dir) / "watch_jobs.json"
            watch_jobs_file.write_text("{broken", encoding="utf-8")

            store = WatchStore(watch_jobs_file)

            self.assertEqual(store.list_jobs(), [])
            self.assertEqual(watch_jobs_file.read_text(encoding="utf-8"), "[]\n")
            backups = list(Path(temp_dir).glob("watch_jobs.corrupt-*.json"))
            self.assertEqual(len(backups), 1)
            diagnostics = store.diagnostics()
            self.assertEqual(diagnostics["recovery_count"], 1)
            self.assertTrue(diagnostics["last_recovery_backup_file"])


class AppReliabilityTests(unittest.TestCase):
    def create_test_client(self) -> TestClient:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        env_overrides = {
            "JOBS_FILE": str(temp_root / "data" / "jobs.json"),
            "WATCH_JOBS_FILE": str(temp_root / "data" / "watch_jobs.json"),
            "DOWNLOADS_FILE": str(temp_root / "data" / "downloads.json"),
            "OUTPUT_DIR": str(temp_root / "output"),
            "LOGS_DIR": str(temp_root / "logs"),
            "RECORDER_DIR": str(temp_root / "vendor" / "recorder"),
            "RECORDER_ENTRYPOINT": str(temp_root / "vendor" / "recorder" / "src" / "main.py"),
            "RECORDER_COOKIES_FILE": str(temp_root / "data" / "cookies.json"),
            "INSTAGRAM_COOKIES_FILE": str(temp_root / "data" / "instagram_cookies.json"),
            "PYTHON_BIN": "python",
            "ROOT_PATH": "",
        }
        env_overrides.update(getattr(self, "extra_env", {}))
        for key, value in env_overrides.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        (temp_root / "vendor" / "recorder" / "src").mkdir(parents=True, exist_ok=True)
        app = create_app()
        self.addCleanup(self.temp_dir.cleanup)
        return TestClient(app)

    def test_watch_validation_returns_structured_error(self) -> None:
        client = self.create_test_client()

        response = client.post("/watch-recordings", json={})

        self.assertEqual(response.status_code, 422)
        body = response.json()
        self.assertIn("detail", body)
        self.assertIn("either username or url must be provided", str(body["detail"]))

    def test_favicon_supports_get_and_head(self) -> None:
        client = self.create_test_client()

        get_response = client.get("/favicon.svg")
        head_response = client.head("/favicon.svg")

        self.assertEqual(get_response.status_code, 200)
        self.assertEqual(head_response.status_code, 200)
        self.assertEqual(get_response.headers.get("content-type"), "image/svg+xml")

    def test_health_details_exposes_runtime_diagnostics(self) -> None:
        client = self.create_test_client()

        response = client.get("/health/details")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertIn("services", body)
        self.assertIn("stores", body)
        self.assertIn("recordings", body)
        self.assertIn("watches", body)
        self.assertIn("thread_alive", body["services"]["watch"])
        self.assertIn("recovery_count", body["stores"]["jobs"])

    def test_recording_and_watch_reject_non_tiktok_urls(self) -> None:
        client = self.create_test_client()

        for path in ("/recordings", "/watch-recordings"):
            response = client.post(path, json={"url": "https://evil.example.com/@x"})
            self.assertEqual(response.status_code, 422, path)
            self.assertIn("TikTok URL", str(response.json()["detail"]), path)

    def test_live_stream_rejects_non_tiktok_url(self) -> None:
        client = self.create_test_client()

        response = client.get("/live/stream", params={"url": "http://169.254.169.254/latest/"})

        self.assertEqual(response.status_code, 422)
        self.assertIn("TikTok URL", str(response.json()["detail"]))

    def test_live_stream_rejects_malformed_username(self) -> None:
        client = self.create_test_client()

        response = client.get("/live/stream", params={"username": 'evil"\nX-Injected: 1'})

        self.assertEqual(response.status_code, 422)

    def test_live_stream_refuses_when_all_relay_slots_are_busy(self) -> None:
        client = self.create_test_client()
        slots = threading.BoundedSemaphore(1)
        client.app.state.live_relay_slots = slots
        self.assertTrue(slots.acquire(blocking=False))
        self.addCleanup(slots.release)

        response = client.get("/live/stream", params={"username": "someone"})

        self.assertEqual(response.status_code, 429)

    def test_production_hides_docs_and_diagnostic_paths(self) -> None:
        self.extra_env = {"APP_ENV": "production"}
        client = self.create_test_client()

        self.assertEqual(client.get("/docs").status_code, 404)
        self.assertEqual(client.get("/openapi.json").status_code, 404)

        body = client.get("/health/details").json()
        self.assertEqual(body["status"], "ok")
        self.assertNotIn("active_processes", body["services"]["recorder"])
        self.assertNotIn("jobs_file", body["stores"]["jobs"])
        self.assertIn("recovery_count", body["stores"]["jobs"])

    def test_downloading_a_recording_stamps_it_instead_of_deleting_it(self) -> None:
        """The file must outlive the download: a save interrupted halfway has to
        be retryable, and a phone that drops Wi-Fi should not destroy the only
        copy."""
        from app.models.recording import RecordingJob, RecordingStatus

        client = self.create_test_client()
        job_store = client.app.state.job_store
        settings = client.app.state.settings

        recording = settings.output_dir / "TK_someone_2026.08.12_10-00-00.mp4"
        recording.write_bytes(b"x" * 32)
        job = RecordingJob(
            username="someone",
            status=RecordingStatus.finished,
            file_path=str(recording),
        )
        job_store.save_job(job)

        response = client.get(f"/recordings/{job.id}/download")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(recording.exists(), "the file must survive being downloaded")
        stamped = job_store.get_job(job.id)
        self.assertIsNotNone(stamped, "the job must survive being downloaded")
        self.assertIsNotNone(stamped.fetched_at, "downloading must stamp fetched_at")

    def test_a_download_entry_can_be_deleted_explicitly(self) -> None:
        from app.models.download import DownloadEntry, DownloadPlatform

        client = self.create_test_client()
        store = client.app.state.download_store
        settings = client.app.state.settings

        download_dir = settings.output_dir / "posts" / "20260812-101500-abc123"
        download_dir.mkdir(parents=True)
        (download_dir / "video.mp4").write_bytes(b"x" * 10)
        store.save_entry(
            DownloadEntry(
                id="20260812-101500-abc123",
                platform=DownloadPlatform.tiktok_post,
                output_dir=str(download_dir),
                files=[str(download_dir / "video.mp4")],
            )
        )

        response = client.delete("/downloads/20260812-101500-abc123")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(download_dir.exists(), "deleting an entry removes its files")
        self.assertIsNone(store.get_entry("20260812-101500-abc123"))
        self.assertEqual(client.delete("/downloads/20260812-101500-abc123").status_code, 404)

    def test_instagram_page_renders_with_session_panel(self) -> None:
        client = self.create_test_client()

        response = client.get("/instagram")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Instagram session", response.text)
        self.assertIn('href="/instagram"', response.text)

    def test_instagram_download_rejects_non_instagram_url(self) -> None:
        client = self.create_test_client()

        empty_response = client.post("/instagram/downloads", json={"url": "   "})
        self.assertEqual(empty_response.status_code, 422)

        wrong_host_response = client.post(
            "/instagram/downloads", json={"url": "https://www.tiktok.com/@x/video/1"}
        )
        self.assertEqual(wrong_host_response.status_code, 422)
        self.assertIn("Instagram URL", str(wrong_host_response.json()["detail"]))

    def test_instagram_unknown_download_returns_404(self) -> None:
        client = self.create_test_client()

        self.assertEqual(client.get("/instagram/downloads/nope").status_code, 404)
        self.assertEqual(client.get("/instagram/downloads/nope/files/0").status_code, 404)

    def test_health_details_exposes_instagram_session(self) -> None:
        client = self.create_test_client()

        body = client.get("/health/details").json()

        self.assertIn("instagram", body)
        self.assertIn("cookies_configured", body["instagram"])
        self.assertIn("browser_login", body["instagram"])


class UrlGuardTests(unittest.TestCase):
    def test_tiktok_url_accepts_known_hosts(self) -> None:
        for url in (
            "https://www.tiktok.com/@example/live",
            "https://vt.tiktok.com/abc123/",
            "http://tiktok.com/@example/video/1",
        ):
            self.assertEqual(validate_tiktok_url(url), url)

    def test_tiktok_url_rejects_other_hosts_and_schemes(self) -> None:
        for url in (
            "https://evil.example.com/@x",
            "https://tiktok.com.evil.example.com/@x",
            "file:///etc/passwd",
            "http://169.254.169.254/latest/meta-data/",
        ):
            with self.assertRaises(ValueError):
                validate_tiktok_url(url)

    def test_public_url_guard_rejects_internal_addresses(self) -> None:
        for url in (
            "http://127.0.0.1/x",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5/x",
            "http://192.168.1.1/x",
            "http://[::1]/x",
            "ftp://93.184.216.34/x",
        ):
            with self.assertRaises(ValueError):
                ensure_public_http_url(url)

    def test_public_url_guard_allows_public_literal(self) -> None:
        url = "https://93.184.216.34/media.mp4"
        self.assertEqual(ensure_public_http_url(url), url)


class RedactionTests(unittest.TestCase):
    def test_query_strings_and_session_values_are_stripped(self) -> None:
        text = (
            "failed to fetch https://webcast.tiktok.com/room/enter/?msToken=SECRET&sig=abc "
            "with session_ss=TOPSECRET"
        )
        redacted = redact_sensitive(text)

        self.assertNotIn("SECRET", redacted)
        self.assertNotIn("TOPSECRET", redacted)
        self.assertIn("https://webcast.tiktok.com/room/enter/?[redacted]", redacted)

    def test_plain_text_is_untouched(self) -> None:
        self.assertEqual(redact_sensitive("recorder exited with code 1"), "recorder exited with code 1")


@unittest.skipIf(os.name == "nt", "POSIX file modes only")
class CookieFilePermissionTests(unittest.TestCase):
    def test_session_file_is_not_world_readable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_file = Path(temp_dir) / "cookies.json"
            service = CookieService(cookie_file)

            self.assertEqual(cookie_file.stat().st_mode & 0o077, 0)

            service.save_session_cookie("a-session-value")
            self.assertEqual(cookie_file.stat().st_mode & 0o077, 0)
            self.assertTrue(service.is_configured())

    def test_temp_cookie_file_for_yt_dlp_is_private(self) -> None:
        from app.services.post_download_service import PostDownloadService

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_root = Path(temp_dir)
            cookie_service = CookieService(temp_root / "cookies.json")
            cookie_service.save_session_cookie("a-session-value")
            service = PostDownloadService(temp_root / "output", cookie_service)

            cookie_file = service._write_cookie_file()
            self.assertIsNotNone(cookie_file)
            try:
                self.assertEqual(cookie_file.stat().st_mode & 0o077, 0)
                self.assertIn("session_ss", cookie_file.read_text(encoding="utf-8"))
            finally:
                cookie_file.unlink(missing_ok=True)


class TikTokCookieNamingTests(unittest.TestCase):
    """TikTok authenticates on sessionid/sessionid_ss and has no session_ss
    cookie; writing only session_ss left the recorder anonymous, so age-gated
    lives were refused even for an account that could watch them."""

    def test_single_value_is_written_under_every_session_name(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_file = Path(temp_dir) / "cookies.json"
            service = CookieService(cookie_file)

            service.save_session_cookie("a-real-session-value")

            stored = json.loads(cookie_file.read_text(encoding="utf-8"))
            self.assertEqual(stored["sessionid"], "a-real-session-value")
            self.assertEqual(stored["sessionid_ss"], "a-real-session-value")
            self.assertEqual(stored["session_ss"], "a-real-session-value")
            self.assertTrue(service.is_configured())

    def test_configured_when_only_sessionid_is_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            cookie_file = Path(temp_dir) / "cookies.json"
            service = CookieService(cookie_file)
            service.save_cookie_map({"sessionid": "x", "tt-target-idc": "alisg"})

            self.assertTrue(service.is_configured())

    def test_full_cookie_map_is_accepted_over_the_api(self) -> None:
        client = AppReliabilityTests.create_test_client(self)

        response = client.post(
            "/auth/tiktok-cookies",
            json={"cookies": {"sessionid": "abc123", "tt-target-idc": "alisg"}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["configured"])

    def test_empty_payload_is_rejected(self) -> None:
        client = AppReliabilityTests.create_test_client(self)

        self.assertEqual(client.post("/auth/tiktok-cookies", json={}).status_code, 422)


class RecorderCookiePathTests(unittest.TestCase):
    def test_default_cookie_path_is_the_one_the_recorder_reads(self) -> None:
        """The vendor opens src/utils/../cookies.json — i.e. src/cookies.json.
        Writing anywhere else silently leaves it unauthenticated."""
        from app.services.config import PROJECT_ROOT, Settings

        for key in ("RECORDER_COOKIES_FILE",):
            os.environ.pop(key, None)
        settings = Settings()

        vendor_src = PROJECT_ROOT / "vendor" / "tiktok-live-recorder" / "src"
        self.assertEqual(settings.recorder_cookies_file.resolve(), (vendor_src / "cookies.json").resolve())


if __name__ == "__main__":
    unittest.main()
