from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services.job_store import JobStore
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
            "OUTPUT_DIR": str(temp_root / "output"),
            "LOGS_DIR": str(temp_root / "logs"),
            "RECORDER_DIR": str(temp_root / "vendor" / "recorder"),
            "RECORDER_ENTRYPOINT": str(temp_root / "vendor" / "recorder" / "src" / "main.py"),
            "RECORDER_COOKIES_FILE": str(temp_root / "data" / "cookies.json"),
            "PYTHON_BIN": "python",
            "ROOT_PATH": "",
        }
        for key, value in env_overrides.items():
            os.environ[key] = value
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


if __name__ == "__main__":
    unittest.main()
