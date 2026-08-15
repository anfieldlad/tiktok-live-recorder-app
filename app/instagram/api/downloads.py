from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

from app.api.security import session_allowed
from app.instagram.services.instagram_download_service import InstagramDownloadResult
from app.models.download import (
    DownloadEntry,
    DownloadJobResponse,
    DownloadPlatform,
    DownloadStatus,
    display_path,
)


router = APIRouter(prefix="/instagram/downloads", tags=["instagram-downloads"])


class InstagramDownloadCreateRequest(BaseModel):
    url: str = Field(min_length=1)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("url must not be empty")
        return normalized


class InstagramDownloadResponse(BaseModel):
    status: str
    download_id: str
    output_dir: str
    files: list[str]
    file_urls: list[str]
    zip_url: str


@router.post("", response_model=None, status_code=status.HTTP_201_CREATED)
def create_download(
    request: Request,
    payload: InstagramDownloadCreateRequest,
    background: bool = Query(default=False, alias="async"),
) -> InstagramDownloadResponse | DownloadJobResponse:
    """Mirrors the TikTok door exactly. See app/api/downloads.py for why both
    doors exist and when the synchronous one can go."""
    job_service = request.app.state.download_job_service
    try:
        entry = job_service.submit(
            payload.url, DownloadPlatform.instagram, use_session=session_allowed(request)
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    if background:
        return DownloadJobResponse.from_entry(entry)

    finished = job_service.wait(entry.id)
    return _synchronous_response(finished)


@router.get("/{download_id}", response_model=InstagramDownloadResponse)
def get_download(request: Request, download_id: str) -> InstagramDownloadResponse:
    instagram_download_service = request.app.state.instagram_download_service
    result = instagram_download_service.get_result(download_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="download not found")
    return _to_response(result)


@router.get("/{download_id}/files/{file_index}")
def download_file(request: Request, download_id: str, file_index: int) -> FileResponse:
    instagram_download_service = request.app.state.instagram_download_service
    try:
        file_path = instagram_download_service.resolve_file(download_id, file_index)
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


@router.get("/{download_id}/zip")
def download_all(request: Request, download_id: str) -> FileResponse:
    instagram_download_service = request.app.state.instagram_download_service
    try:
        archive_path = instagram_download_service.create_archive(download_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return FileResponse(
        path=archive_path,
        filename=f"instagram-{download_id}.zip",
        media_type="application/zip",
        background=BackgroundTask(_remove_archive_and_stamp, request, download_id, archive_path),
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


def _synchronous_response(entry: DownloadEntry | None) -> InstagramDownloadResponse:
    """The exact payload the shipped Android build parses. Do not add fields."""
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="the download disappeared"
        )
    if entry.status != DownloadStatus.finished:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=entry.error or "the download failed"
        )
    return InstagramDownloadResponse(
        status="finished",
        download_id=entry.id,
        output_dir=display_path(entry.output_dir),
        files=[display_path(path) for path in entry.files],
        file_urls=[
            f"/instagram/downloads/{entry.id}/files/{index}" for index, _ in enumerate(entry.files)
        ],
        zip_url=f"/instagram/downloads/{entry.id}/zip",
    )


def _remove_archive_and_stamp(request: Request, download_id: str, archive_path: Path) -> None:
    """The zip is a temp artifact and always goes; the media it was built from
    is stamped like any other fetch and swept later."""
    archive_path.unlink(missing_ok=True)
    request.app.state.download_store.mark_fetched(download_id)


def _to_response(result: InstagramDownloadResult) -> InstagramDownloadResponse:
    return InstagramDownloadResponse(
        status="finished",
        download_id=result.download_id,
        output_dir=display_path(result.output_dir),
        files=[display_path(file_path) for file_path in result.files],
        file_urls=[f"/instagram/downloads/{result.download_id}/files/{index}" for index, _ in enumerate(result.files)],
        zip_url=f"/instagram/downloads/{result.download_id}/zip",
    )
