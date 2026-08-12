from __future__ import annotations

import os
import unittest
from datetime import timedelta

from app.models.recording import utc_now
from app.services.config import Settings
from app.services.retention import RetentionPolicy


class RetentionPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        for key in (
            "RETENTION_FETCHED_HOURS",
            "RETENTION_ORPHAN_HOURS",
            "CLEANUP_MAX_AGE_HOURS",
            "LOG_MAX_AGE_HOURS",
            "STORAGE_SOFT_LIMIT_GB",
        ):
            os.environ.pop(key, None)
            self.addCleanup(os.environ.pop, key, None)

    def test_defaults(self) -> None:
        policy = RetentionPolicy.from_settings(Settings())

        self.assertEqual(policy.fetched_hours, 24)
        self.assertEqual(policy.orphan_hours, 24)
        self.assertEqual(policy.log_hours, 72)
        self.assertEqual(policy.storage_soft_limit_bytes, 20 * 1024**3)

    def test_cleanup_max_age_is_the_fallback_for_both_windows(self) -> None:
        os.environ["CLEANUP_MAX_AGE_HOURS"] = "6"

        policy = RetentionPolicy.from_settings(Settings())

        self.assertEqual(policy.fetched_hours, 6)
        self.assertEqual(policy.orphan_hours, 6)

    def test_explicit_windows_win_over_the_fallback(self) -> None:
        os.environ["CLEANUP_MAX_AGE_HOURS"] = "6"
        os.environ["RETENTION_FETCHED_HOURS"] = "48"

        policy = RetentionPolicy.from_settings(Settings())

        self.assertEqual(policy.fetched_hours, 48)
        self.assertEqual(policy.orphan_hours, 6)

    def test_never_fetched_never_expires(self) -> None:
        policy = RetentionPolicy.from_settings(Settings())

        self.assertFalse(policy.is_expired(None, policy.fetched_hours))

    def test_expiry_is_measured_from_the_timestamp(self) -> None:
        policy = RetentionPolicy.from_settings(Settings())
        just_now = utc_now()
        long_ago = utc_now() - timedelta(hours=25)

        self.assertFalse(policy.is_expired(just_now, policy.fetched_hours))
        self.assertTrue(policy.is_expired(long_ago, policy.fetched_hours))


if __name__ == "__main__":
    unittest.main()
