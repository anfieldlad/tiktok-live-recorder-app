from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator, model_validator

from app.services.url_guard import validate_tiktok_url


USERNAME_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._"
)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def normalize_username(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    normalized = value.strip().lstrip("@")
    if not normalized:
        return None
    if any(char not in USERNAME_CHARACTERS for char in normalized):
        raise ValueError("username may only contain letters, numbers, dots, and underscores")
    return normalized


def normalize_source_url(value: Optional[HttpUrl]) -> Optional[HttpUrl]:
    # The URL ends up in a recorder argv and in the vendor HTTP client, so it has
    # to point at TikTok and nowhere else.
    if value is None:
        return None
    validate_tiktok_url(str(value), label="url")
    return value


class RecordingStatus(str, Enum):
    queued = "queued"
    running = "running"
    finished = "finished"
    failed = "failed"
    stopped = "stopped"


class RecordingProgress(str, Enum):
    preparing = "preparing"
    recording = "recording"
    finalizing = "finalizing"
    ready = "ready"
    failed = "failed"
    stopped = "stopped"


class RecordingCreateRequest(BaseModel):
    username: Optional[str] = Field(default=None, max_length=64)
    url: Optional[HttpUrl] = None
    duration: Optional[int] = Field(default=None, ge=1)

    @field_validator("username")
    @classmethod
    def check_username(cls, value: Optional[str]) -> Optional[str]:
        return normalize_username(value)

    @field_validator("url")
    @classmethod
    def check_url(cls, value: Optional[HttpUrl]) -> Optional[HttpUrl]:
        return normalize_source_url(value)

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
    progress: RecordingProgress = RecordingProgress.preparing
    progress_message: str = "Preparing the recorder."
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

    def resolved_progress(self) -> RecordingProgress:
        if self.status == RecordingStatus.finished:
            return RecordingProgress.ready
        if self.status == RecordingStatus.failed:
            return RecordingProgress.failed
        if self.status == RecordingStatus.stopped:
            return RecordingProgress.stopped
        if self.status == RecordingStatus.running:
            return RecordingProgress.recording
        return self.progress

    def resolved_progress_message(self) -> str:
        if self.status == RecordingStatus.finished:
            return "Recording finished and ready to download."
        if self.status == RecordingStatus.failed:
            return self.error or "The recording ended with an error."
        if self.status == RecordingStatus.stopped:
            return "Recording stopped."
        if self.status == RecordingStatus.running:
            return "Recording is in progress."
        return self.progress_message


class RecordingJobResponse(BaseModel):
    id: str
    username: Optional[str]
    url: Optional[str]
    status: RecordingStatus
    duration: Optional[int]
    file_path: Optional[str]
    file_name: Optional[str]
    file_size_bytes: Optional[int]
    pid: Optional[int]
    error: Optional[str]
    progress: RecordingProgress
    progress_message: str
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
            file_size_bytes=Path(job.file_path).stat().st_size if job.file_path and Path(job.file_path).exists() else None,
            pid=job.pid,
            error=job.error,
            progress=job.resolved_progress(),
            progress_message=job.resolved_progress_message(),
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


class WatchStatus(str, Enum):
    watching = "watching"
    recording = "recording"
    completed = "completed"
    failed = "failed"
    stopped = "stopped"


class WatchCreateRequest(BaseModel):
    username: Optional[str] = Field(default=None, max_length=64)
    url: Optional[HttpUrl] = None
    duration: Optional[int] = Field(default=None, ge=1)

    @field_validator("username")
    @classmethod
    def check_watch_username(cls, value: Optional[str]) -> Optional[str]:
        return normalize_username(value)

    @field_validator("url")
    @classmethod
    def check_watch_url(cls, value: Optional[HttpUrl]) -> Optional[HttpUrl]:
        return normalize_source_url(value)

    @model_validator(mode="after")
    def validate_watch_source(self) -> "WatchCreateRequest":
        if not self.username and not self.url:
            raise ValueError("either username or url must be provided")
        if self.username and self.url:
            raise ValueError("provide either username or url, not both")
        return self


class WatchJob(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    username: Optional[str] = None
    url: Optional[str] = None
    duration: Optional[int] = None
    status: WatchStatus = WatchStatus.watching
    linked_recording_job_id: Optional[str] = None
    last_checked_at: Optional[datetime] = None
    last_message: str = "Waiting for the account to go live."
    created_at: datetime = Field(default_factory=utc_now)
    finished_at: Optional[datetime] = None


class WatchJobResponse(BaseModel):
    id: str
    username: Optional[str]
    url: Optional[str]
    duration: Optional[int]
    status: WatchStatus
    linked_recording_job_id: Optional[str]
    last_checked_at: Optional[datetime]
    last_message: str
    created_at: datetime
    finished_at: Optional[datetime]

    @classmethod
    def from_job(cls, job: WatchJob) -> "WatchJobResponse":
        return cls(**job.model_dump(mode="json"))


class LiveStatusResponse(BaseModel):
    username: Optional[str] = None
    url: Optional[str] = None
    room_id: Optional[str] = None
    is_live: bool
    can_record: bool = False
    message: str


class TikTokCookieRequest(BaseModel):
    """Either one session value, or the browser's whole TikTok cookie jar.

    The full map is preferred: restricted lives need more than the session id
    (`tt-target-idc` routes to the right data centre, `sid_guard` and friends
    carry the login), and passing the browser's real cookie names is what the
    recorder ends up sending.
    """

    session_ss: Optional[str] = Field(default=None, min_length=10)
    cookies: Optional[dict[str, str]] = None

    @field_validator("session_ss")
    @classmethod
    def normalize_session(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("cookies")
    @classmethod
    def normalize_cookies(cls, value: Optional[dict[str, str]]) -> Optional[dict[str, str]]:
        if not value:
            return None
        cleaned = {str(k).strip(): str(v) for k, v in value.items() if str(k).strip() and v}
        return cleaned or None

    @model_validator(mode="after")
    def validate_payload(self) -> "TikTokCookieRequest":
        if not self.session_ss and not self.cookies:
            raise ValueError("provide session_ss or a cookies map")
        return self


class TikTokCookieStatusResponse(BaseModel):
    configured: bool
    cookie_file: str


class TikTokBrowserLoginStatusResponse(BaseModel):
    browser_open: bool
    browser_name: Optional[str] = None
    authenticated: bool
    cookies_configured: bool
    browser_launch_supported: bool
