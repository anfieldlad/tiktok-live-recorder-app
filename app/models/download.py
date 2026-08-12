from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from app.models.recording import utc_now


class DownloadPlatform(str, Enum):
    tiktok_post = "tiktok_post"
    instagram = "instagram"


class DownloadEntry(BaseModel):
    """One completed download, and whether the user has been given it yet.

    `fetched_at` is the whole point of persisting this: without it, a restart
    left files on disk that nothing could serve and nothing would remove.
    """

    id: str
    platform: DownloadPlatform
    output_dir: str
    files: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    fetched_at: Optional[datetime] = None
