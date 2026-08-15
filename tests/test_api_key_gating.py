from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


TEST_KEY = "0123456789abcdef0123456789abcdef"


class GatingTestCase(unittest.TestCase):
    """Every test builds its own app: enforcement is read from Settings at
    construction, so the key has to be in the environment before create_app()."""

    api_key: str | None = None

    def create_test_client(self) -> TestClient:
        self.temp_dir = tempfile.TemporaryDirectory()
        temp_root = Path(self.temp_dir.name)
        env = {
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
            "API_KEY": self.api_key or "",
        }
        for key, value in env.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        (temp_root / "vendor" / "recorder" / "src").mkdir(parents=True, exist_ok=True)
        self.app = create_app()
        # Cleanups run last-registered-first: join the pool before the directory
        # it writes into is removed.
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(self.app.state.download_job_service.stop)
        self.stub_fetchers()
        return TestClient(self.app)

    def stub_fetchers(self) -> None:
        """No test may shell out to yt-dlp. See tests/test_download_api.py."""

        def nothing(url: str, download_id: str | None = None, use_session: bool = True):
            return None

        for service in (self.app.state.post_download_service, self.app.state.instagram_download_service):
            service.download = nothing


class TierOneTests(GatingTestCase):
    api_key = TEST_KEY

    def test_saving_a_session_without_the_header_is_401(self) -> None:
        client = self.create_test_client()

        response = client.post("/auth/tiktok-cookies", json={"session_ss": "a" * 20})

        self.assertEqual(response.status_code, 401)

    def test_a_wrong_key_is_401(self) -> None:
        client = self.create_test_client()

        response = client.post(
            "/auth/tiktok-cookies",
            json={"session_ss": "a" * 20},
            headers={"X-API-Key": "wrong"},
        )

        self.assertEqual(response.status_code, 401)

    def test_the_right_key_gets_through(self) -> None:
        client = self.create_test_client()

        response = client.post(
            "/auth/tiktok-cookies",
            json={"session_ss": "a" * 20},
            headers={"X-API-Key": TEST_KEY},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["configured"])

    def test_clearing_a_session_is_gated_too(self) -> None:
        client = self.create_test_client()

        self.assertEqual(client.delete("/auth/tiktok-cookies").status_code, 401)
        self.assertEqual(client.delete("/instagram/auth/cookies").status_code, 401)

    def test_instagram_session_writes_are_gated(self) -> None:
        client = self.create_test_client()

        response = client.post("/instagram/auth/cookies", json={"sessionid": "abc"})

        self.assertEqual(response.status_code, 401)

    def test_browser_login_is_gated(self) -> None:
        client = self.create_test_client()

        self.assertEqual(client.post("/auth/login-browser/chrome/start").status_code, 401)
        self.assertEqual(client.post("/auth/login-browser/capture").status_code, 401)
        self.assertEqual(client.post("/auth/login-browser/close").status_code, 401)
        self.assertEqual(client.post("/auth/import-browser/chrome").status_code, 401)
        self.assertEqual(client.post("/instagram/auth/login-browser/chrome/start").status_code, 401)
        self.assertEqual(client.post("/instagram/auth/login-browser/capture").status_code, 401)
        self.assertEqual(client.post("/instagram/auth/login-browser/close").status_code, 401)
        self.assertEqual(client.post("/instagram/auth/import-browser/chrome").status_code, 401)


class EnforcementOffTests(GatingTestCase):
    api_key = None

    def test_with_no_key_configured_a_tier_one_route_is_open(self) -> None:
        """A missing env var must degrade to today's behaviour, not lock everyone out."""
        client = self.create_test_client()

        response = client.post("/auth/tiktok-cookies", json={"session_ss": "a" * 20})

        self.assertEqual(response.status_code, 200)

    def test_status_is_unredacted_when_enforcement_is_off(self) -> None:
        client = self.create_test_client()

        body = client.get("/auth/status").json()

        self.assertTrue(body["cookie_file"])
        self.assertTrue(body["session_allowed"])


class StatusRedactionTests(GatingTestCase):
    api_key = TEST_KEY

    def test_status_is_open_but_redacted_without_the_key(self) -> None:
        client = self.create_test_client()

        body = client.get("/auth/status").json()

        self.assertIsNone(body["cookie_file"], "a path is not an anonymous caller's business")
        self.assertFalse(body["session_allowed"])
        self.assertIn("configured", body)

    def test_status_is_complete_with_the_key(self) -> None:
        client = self.create_test_client()

        body = client.get("/auth/status", headers={"X-API-Key": TEST_KEY}).json()

        self.assertTrue(body["cookie_file"])
        self.assertTrue(body["session_allowed"])

    def test_instagram_status_redacts_the_same_way(self) -> None:
        client = self.create_test_client()

        body = client.get("/instagram/auth/status").json()

        self.assertIsNone(body["cookie_file"])
        self.assertFalse(body["session_allowed"])

    def test_browser_login_status_stays_open(self) -> None:
        """It reports only whether guided login works on this platform."""
        client = self.create_test_client()

        self.assertEqual(client.get("/auth/login-browser/status").status_code, 200)
        self.assertEqual(client.get("/instagram/auth/login-browser/status").status_code, 200)


class TierThreeTests(GatingTestCase):
    api_key = TEST_KEY

    def test_pages_and_listings_are_untouched(self) -> None:
        """Guards against over-gating: the site stays as publicly loadable as it was."""
        client = self.create_test_client()

        for path in ["/", "/watch", "/download", "/health", "/recordings", "/watch-recordings", "/downloads"]:
            with self.subTest(path=path):
                self.assertEqual(client.get(path).status_code, 200)

    def test_deletes_stay_open_by_decision(self) -> None:
        """An accepted risk, recorded in the spec. Not an oversight."""
        client = self.create_test_client()

        response = client.delete("/downloads/does-not-exist")

        self.assertEqual(response.status_code, 404, "404 means it reached the handler, not 401")


class TierTwoTests(GatingTestCase):
    """The assertions that distinguish this design from simply blocking the
    endpoint. Both are easy to get silently wrong."""

    api_key = TEST_KEY

    def test_a_download_without_the_key_is_accepted_but_anonymous(self) -> None:
        client = self.create_test_client()

        response = client.post(
            "/downloads?async=1", json={"url": "https://www.tiktok.com/@a/video/1"}
        )

        self.assertEqual(response.status_code, 201, "Tier 2 is open, not blocked")
        entry = self.app.state.download_store.get_entry(response.json()["id"])
        self.assertFalse(entry.use_session, "an anonymous fetch must not spend the session")

    def test_a_download_with_the_key_runs_as_the_account_holder(self) -> None:
        client = self.create_test_client()

        response = client.post(
            "/downloads?async=1",
            json={"url": "https://www.tiktok.com/@a/video/1"},
            headers={"X-API-Key": TEST_KEY},
        )

        self.assertEqual(response.status_code, 201)
        entry = self.app.state.download_store.get_entry(response.json()["id"])
        self.assertTrue(entry.use_session)

    def test_instagram_downloads_carry_the_same_decision(self) -> None:
        client = self.create_test_client()

        anonymous = client.post(
            "/instagram/downloads?async=1", json={"url": "https://www.instagram.com/p/abc/"}
        )
        authorised = client.post(
            "/instagram/downloads?async=1",
            json={"url": "https://www.instagram.com/p/def/"},
            headers={"X-API-Key": TEST_KEY},
        )

        store = self.app.state.download_store
        self.assertFalse(store.get_entry(anonymous.json()["id"]).use_session)
        self.assertTrue(store.get_entry(authorised.json()["id"]).use_session)

    def test_the_worker_is_handed_the_decision_the_request_made(self) -> None:
        """The fetch happens on another thread; the flag has to survive the trip."""
        client = self.create_test_client()
        seen: list[bool] = []

        def recording_download(url: str, download_id: str | None = None, use_session: bool = True):
            seen.append(use_session)
            return None

        self.app.state.post_download_service.download = recording_download

        first = client.post("/downloads?async=1", json={"url": "https://www.tiktok.com/@a/video/1"})
        self.app.state.download_job_service.wait(first.json()["id"], timeout=5)
        second = client.post(
            "/downloads?async=1",
            json={"url": "https://www.tiktok.com/@a/video/2"},
            headers={"X-API-Key": TEST_KEY},
        )
        self.app.state.download_job_service.wait(second.json()["id"], timeout=5)

        self.assertEqual(seen, [False, True])

    def test_check_live_is_open_and_passes_the_decision_down(self) -> None:
        client = self.create_test_client()
        seen: list[bool] = []

        def fake_check(payload, use_session: bool = True):
            seen.append(use_session)
            from app.models.recording import LiveStatusResponse

            return LiveStatusResponse(is_live=False, can_record=False, message="not live")

        self.app.state.live_status_service.check = fake_check

        self.assertEqual(client.post("/recordings/check-live", json={"username": "a"}).status_code, 200)
        client.post(
            "/recordings/check-live", json={"username": "a"}, headers={"X-API-Key": TEST_KEY}
        )

        self.assertEqual(seen, [False, True])

    def test_starting_a_recording_is_open_and_gated_by_the_live_check(self) -> None:
        """The vendor recorder reads a fixed cookie path, so the enforcement
        point for recordings is the check that decides `can_record`."""
        client = self.create_test_client()
        seen: list[bool] = []

        def fake_check(payload, use_session: bool = True):
            seen.append(use_session)
            from app.models.recording import LiveStatusResponse

            return LiveStatusResponse(is_live=False, can_record=False, message="not live")

        self.app.state.live_status_service.check = fake_check

        response = client.post("/recordings", json={"username": "a"})

        self.assertEqual(response.status_code, 400, "not live — but it reached the handler")
        self.assertEqual(seen, [False])


if __name__ == "__main__":
    unittest.main()
