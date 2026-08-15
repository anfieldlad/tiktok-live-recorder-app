from __future__ import annotations

import secrets
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from app.models.recording import utc_now
from app.services.config import PROJECT_ROOT


class DownloadPlatform(str, Enum):
    tiktok_post = "tiktok_post"
    instagram = "instagram"


class DownloadStatus(str, Enum):
    queued = "queued"
    running = "running"
    finished = "finished"
    failed = "failed"


def new_download_id() -> str:
    """A sortable, collision-resistant id, allocated before the fetch starts.

    Both fetchers used to mint this themselves at the moment they began. A job
    needs its id at submit time so the caller has something to poll.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{secrets.token_hex(3)}"


def display_path(path: str | Path) -> str:
    """Project-relative where possible, so the register does not publish the
    server's filesystem layout."""
    resolved = Path(path).resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)


class DownloadEntry(BaseModel):
    """One download, from the moment it is queued to the moment it is swept.

    This used to describe only *completed* work. It now carries the same
    lifecycle fields RecordingJob does, so queued and running downloads are
    visible to the UI instead of existing only inside a request that has not
    returned yet.

    `fetched_at` remains the retention key: without it, a restart left files on
    disk that nothing could serve and nothing would remove. A queued or failed
    entry has no files and no `fetched_at`, and the sweep must skip it.

    Every new field has a default, so rows written before the job model load
    unchanged and read as `finished`.
    """

    id: str
    platform: DownloadPlatform
    status: DownloadStatus = DownloadStatus.finished
    url: Optional[str] = None
    output_dir: str = ""
    files: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None

    def is_terminal(self) -> bool:
        return self.status in {DownloadStatus.finished, DownloadStatus.failed}
