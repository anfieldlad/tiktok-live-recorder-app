"""Every duration the cleanup sweep obeys, in one place.

Gathering them here keeps hour literals out of the sweep and lets a test build
a policy with second-scale windows instead of back-dating file mtimes.

One rule is deliberately absent: an item that was never fetched is never
swept. That is not a duration and must not become configurable — a config
mistake should not be able to destroy media the user has not seen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.recording import utc_now
from app.services.config import Settings


@dataclass(frozen=True)
class RetentionPolicy:
    fetched_hours: float
    orphan_hours: float
    log_hours: float
    interval_seconds: int
    storage_soft_limit_bytes: int

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetentionPolicy":
        fallback = float(settings.cleanup_max_age_hours)
        return cls(
            fetched_hours=float(settings.retention_fetched_hours or fallback),
            orphan_hours=float(settings.retention_orphan_hours or fallback),
            log_hours=float(settings.log_max_age_hours),
            interval_seconds=int(settings.cleanup_interval_minutes * 60),
            storage_soft_limit_bytes=int(settings.storage_soft_limit_gb * 1024**3),
        )

    def is_expired(self, timestamp: datetime | None, hours: float) -> bool:
        """True only when `timestamp` is set and older than `hours`."""
        if timestamp is None:
            return False
        return timestamp < utc_now() - timedelta(hours=hours)

    def is_older_than(self, mtime: float, hours: float) -> bool:
        """True when a filesystem mtime (epoch seconds) is older than `hours`."""
        return mtime < (utc_now() - timedelta(hours=hours)).timestamp()
