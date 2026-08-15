from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.models.download import DownloadEntry, DownloadPlatform, DownloadStatus


class DownloadApiTests(unittest.TestCase):
    def create_test_client(self) -> TestClient:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        for key, value in {
            "JOBS_FILE": str(temp_root / "data" / "jobs.json"),
            "WATCH_JOBS_FILE": str(temp_root / "data" / "watch_jobs.json"),
            "DOWNLOADS_FILE": str(temp_root / "data" / "downloads.json"),
            "OUTPUT_DIR": str(temp_root / "output"),
            "LOGS_DIR": str(temp_root / "logs"),
            "RECORDER_DIR": str(temp_root / "vendor" / "recorder"),
            "RECORDER_ENTRYPOINT": str(temp_root / "vendor" / "recorder" / "src" / "main.py"),
            "RECORDER_COOKIES_FILE": str(temp_root / "data" / "cookies.json"),
            "INSTAGRAM_COOKIES_FILE": str(temp_root / "data" / "instagram_cookies.json"),
            "ROOT_PATH": "",
        }.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        (temp_root / "vendor" / "recorder" / "src").mkdir(parents=True, exist_ok=True)
        app = create_app()
        # Cleanups run last-registered-first: join the pool before the directory
        # it writes into is removed.
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(app.state.download_job_service.stop)
        self.app = app
        self.stub_fetchers()
        return TestClient(app)

    def stub_fetchers(self) -> None:
        """No test may shell out to yt-dlp.

        Submitting to the async door hands the job to a real worker, which
        without this runs the real fetcher against the test's fake URL — 15
        seconds of network per test, and a flaky one at that. Tests that care
        about what the fetcher produced override this with their own fake.
        """

        def nothing(url: str, download_id: str | None = None, use_session: bool = True):
            return None

        for service in (self.app.state.post_download_service, self.app.state.instagram_download_service):
            service.download = nothing

    def test_the_async_door_returns_a_queued_job_immediately(self) -> None:
        client = self.create_test_client()

        response = client.post(
            "/downloads?async=1", json={"url": "https://www.tiktok.com/@someone/video/123"}
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "queued")
        self.assertTrue(body["id"])
        self.assertEqual(body["platform"], "tiktok_post")
        self.assertEqual(body["files"], [])

    def test_a_bad_url_is_still_a_422_on_the_async_door(self) -> None:
        client = self.create_test_client()

        response = client.post("/downloads?async=1", json={"url": "https://example.com/x"})

        self.assertEqual(response.status_code, 422)

    def test_the_list_shows_in_flight_work(self) -> None:
        client = self.create_test_client()
        store = self.app.state.download_store
        store.save_entry(
            DownloadEntry(
                id="20260815-101500-aaaaaa",
                platform=DownloadPlatform.instagram,
                status=DownloadStatus.running,
                url="https://www.instagram.com/p/abc/",
            )
        )

        body = client.get("/downloads").json()

        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["status"], "running")
        self.assertEqual(body[0]["platform"], "instagram")
        self.assertEqual(body[0]["file_urls"], [])

    def test_a_finished_entry_carries_platform_correct_urls(self) -> None:
        client = self.create_test_client()
        store = self.app.state.download_store
        output_dir = Path(self.temp_dir.name) / "output" / "instagram" / "20260815-101500-bbbbbb"
        output_dir.mkdir(parents=True)
        media = output_dir / "reel.mp4"
        media.write_bytes(b"x")
        store.save_entry(
            DownloadEntry(
                id="20260815-101500-bbbbbb",
                platform=DownloadPlatform.instagram,
                status=DownloadStatus.finished,
                output_dir=str(output_dir),
                files=[str(media)],
            )
        )

        body = client.get("/downloads").json()

        self.assertEqual(
            body[0]["file_urls"], ["/instagram/downloads/20260815-101500-bbbbbb/files/0"]
        )
        self.assertEqual(body[0]["zip_url"], "/instagram/downloads/20260815-101500-bbbbbb/zip")

    def test_a_tiktok_entry_has_no_zip_url(self) -> None:
        client = self.create_test_client()
        store = self.app.state.download_store
        store.save_entry(
            DownloadEntry(
                id="20260815-101500-cccccc",
                platform=DownloadPlatform.tiktok_post,
                status=DownloadStatus.finished,
                output_dir="/tmp/x",
                files=["/tmp/x/video.mp4"],
            )
        )

        body = client.get("/downloads").json()

        self.assertIsNone(body[0]["zip_url"])
        self.assertEqual(body[0]["file_urls"], ["/downloads/20260815-101500-cccccc/files/0"])


class SynchronousDoorContractTests(DownloadApiTests):
    """Android's DownloadResponse defaults every field, so a shape regression
    shows as zero files rather than an error. Assert the payload, not the 200."""

    def test_the_synchronous_payload_keeps_every_field_android_reads(self) -> None:
        client = self.create_test_client()
        service = self.app.state.post_download_service
        output_dir = service.output_dir / "20260815-101500-dddddd"
        output_dir.mkdir(parents=True)
        media = output_dir / "video.mp4"
        media.write_bytes(b"x")

        # Drive the real route, but with a fetcher that does nothing but write
        # the files a successful yt-dlp run would have left behind.
        def fake_download(url: str, download_id: str | None = None, use_session: bool = True):
            from app.services.post_download_service import PostDownloadResult

            return service.remember(
                PostDownloadResult(
                    download_id=download_id, output_dir=output_dir, files=[media]
                )
            )

        original = service.download
        service.download = fake_download
        self.addCleanup(lambda: setattr(service, "download", original))

        response = client.post(
            "/downloads", json={"url": "https://www.tiktok.com/@someone/video/123"}
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(
            sorted(body.keys()), ["download_id", "file_urls", "files", "output_dir", "status"]
        )
        self.assertEqual(body["status"], "finished")
        self.assertTrue(body["download_id"])
        self.assertEqual(len(body["files"]), 1)
        self.assertEqual(body["file_urls"], [f"/downloads/{body['download_id']}/files/0"])

    def test_a_failing_fetch_is_still_a_400_on_the_synchronous_door(self) -> None:
        client = self.create_test_client()
        service = self.app.state.post_download_service

        def fake_download(url: str, download_id: str | None = None, use_session: bool = True):
            raise RuntimeError("This post is no longer available on TikTok.")

        original = service.download
        service.download = fake_download
        self.addCleanup(lambda: setattr(service, "download", original))

        response = client.post(
            "/downloads", json={"url": "https://www.tiktok.com/@someone/video/123"}
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no longer available", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
