from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RecordingStatus(str, Enum):
    queued = "queued"
    running = "running"
    finished = "finished"
    failed = "failed"
    stopped = "stopped"


class RecordingCreateRequest(BaseModel):
    username: Optional[str] = Field(default=None, max_length=64)
    url: Optional[HttpUrl] = None
    duration: Optional[int] = Field(default=None, ge=1)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lstrip("@")
        if not normalized:
            return None
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._")
        if any(char not in allowed for char in normalized):
            raise ValueError("username may only contain letters, numbers, dots, and underscores")
        return normalized

    @model_validator(mode="after")
    def validate_source(self) -> "RecordingCreateRequest":
        if not self.username and not self.url:
            raise ValueError("either username or url must be provided")
        if self.username and self.url:
            raise ValueError("provide either username or url, not both")
        return self


class RecordingJob(BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    id: str = Field(default_factory=lambda: str(uuid4()))
    username: Optional[str] = None
    url: Optional[str] = None
    status: RecordingStatus = RecordingStatus.queued
    duration: Optional[int] = None
    file_path: Optional[str] = None
    pid: Optional[int] = None
    error: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None

    @property
    def file_name(self) -> Optional[str]:
        if not self.file_path:
            return None
        return Path(self.file_path).name

    def is_downloadable(self) -> bool:
        return self.status in {RecordingStatus.finished, RecordingStatus.stopped} and bool(self.file_path)


class RecordingJobResponse(BaseModel):
    id: str
    username: Optional[str]
    url: Optional[str]
    status: RecordingStatus
    duration: Optional[int]
    file_path: Optional[str]
    file_name: Optional[str]
    pid: Optional[int]
    error: Optional[str]
    created_at: datetime
    started_at: Optional[datetime]
    finished_at: Optional[datetime]

    @classmethod
    def from_job(cls, job: RecordingJob) -> "RecordingJobResponse":
        return cls(
            id=job.id,
            username=job.username,
            url=job.url,
            status=job.status,
            duration=job.duration,
            file_path=job.file_path,
            file_name=job.file_name,
            pid=job.pid,
            error=job.error,
            created_at=job.created_at,
            started_at=job.started_at,
            finished_at=job.finished_at,
        )


class RecordingCreateResponse(BaseModel):
    id: str
    status: RecordingStatus


class RecordingActionResponse(BaseModel):
    id: str
    status: RecordingStatus
    file_path: Optional[str] = None
    error: Optional[str] = None


class LiveStatusResponse(BaseModel):
    username: Optional[str] = None
    url: Optional[str] = None
    room_id: Optional[str] = None
    is_live: bool
    can_record: bool = False
    message: str


class TikTokCookieRequest(BaseModel):
    session_ss: str = Field(min_length=10)

    @field_validator("session_ss")
    @classmethod
    def normalize_session(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("session_ss is required")
        return normalized


class TikTokCookieStatusResponse(BaseModel):
    configured: bool
    cookie_file: str


class TikTokBrowserLoginStatusResponse(BaseModel):
    browser_open: bool
    browser_name: Optional[str] = None
    authenticated: bool
    cookies_configured: bool
