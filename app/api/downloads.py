from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field, field_validator

from app.models.download import (
    DownloadEntry,
    DownloadJobResponse,
    DownloadPlatform,
    DownloadStatus,
    display_path,
)
from app.services.post_download_service import PostDownloadResult


router = APIRouter(prefix="/downloads", tags=["downloads"])


class PostDownloadCreateRequest(BaseModel):
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("url must not be empty")
        return normalized


class PostDownloadCreateResponse(BaseModel):
    status: str
    download_id: str
    output_dir: str
    files: list[str]
    file_urls: list[str]


class PostDownloadResponse(BaseModel):
    status: str
    download_id: str
    output_dir: str
    files: list[str]
    file_urls: list[str]


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_download(
    request: Request,
    payload: PostDownloadCreateRequest,
    background: bool = Query(default=False, alias="async"),
) -> PostDownloadCreateResponse | DownloadJobResponse:
    """Two doors onto one queue.

    `?async=1` is the register's door: it returns a job id to poll. The bare
    POST is the shim the shipped Android app uses — it submits the same job and
    holds the connection until it finishes, returning the payload that build
    parses. Delete it once Still Here mobile ships against the async door.
    """
    job_service = request.app.state.download_job_service
    try:
        entry = job_service.submit(payload.url, DownloadPlatform.tiktok_post)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if background:
        return DownloadJobResponse.from_entry(entry)

    finished = job_service.wait(entry.id)
    return _synchronous_response(finished)


@router.get("", response_model=list[DownloadJobResponse])
def list_downloads(request: Request) -> list[DownloadJobResponse]:
    """Every entry, both platforms, newest first — the register's Filed list."""
    store = request.app.state.download_store
    return [DownloadJobResponse.from_entry(entry) for entry in store.list_entries()]


@router.get("/{download_id}", response_model=PostDownloadResponse)
def get_download(request: Request, download_id: str) -> PostDownloadResponse:
    post_download_service = request.app.state.post_download_service
    result = post_download_service.get_result(download_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="download not found")

    return PostDownloadResponse(
        status="finished",
        download_id=result.download_id,
        output_dir=display_path(result.output_dir),
        files=[display_path(file_path) for file_path in result.files],
        file_urls=_file_urls(result),
    )


@router.get("/{download_id}/files/{file_index}")
def download_file(request: Request, download_id: str, file_index: int) -> FileResponse:
    post_download_service = request.app.state.post_download_service
    try:
        file_path = post_download_service.resolve_file(download_id, file_index)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return FileResponse(
        path=file_path,
        filename=file_path.name,
        media_type="application/octet-stream",
        background=BackgroundTask(request.app.state.download_store.mark_fetched, download_id),
    )


@router.delete("/{download_id}")
def delete_download(request: Request, download_id: str) -> dict[str, bool]:
    """Remove a download and its files now, rather than waiting for the sweep."""
    store = request.app.state.download_store
    entry = store.get_entry(download_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="download not found")
    if entry.output_dir:
        shutil.rmtree(Path(entry.output_dir), ignore_errors=True)
    store.delete_entry(download_id)
    return {"deleted": True}


def _synchronous_response(entry: DownloadEntry | None) -> PostDownloadCreateResponse:
    """The exact payload the shipped Android build parses. Do not add fields."""
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="the download disappeared"
        )
    if entry.status != DownloadStatus.finished:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=entry.error or "the download failed"
        )
    return PostDownloadCreateResponse(
        status="finished",
        download_id=entry.id,
        output_dir=display_path(entry.output_dir),
        files=[display_path(path) for path in entry.files],
        file_urls=[f"/downloads/{entry.id}/files/{index}" for index, _ in enumerate(entry.files)],
    )


def _file_urls(result: PostDownloadResult) -> list[str]:
    return [f"/downloads/{result.download_id}/files/{index}" for index, _ in enumerate(result.files)]
