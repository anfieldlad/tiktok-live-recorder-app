from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field, field_validator

from app.services.config import PROJECT_ROOT
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


@router.post("", response_model=PostDownloadCreateResponse, status_code=status.HTTP_201_CREATED)
def create_download(request: Request, payload: PostDownloadCreateRequest) -> PostDownloadCreateResponse:
    post_download_service = request.app.state.post_download_service
    try:
        result = post_download_service.download(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return PostDownloadCreateResponse(
        status="finished",
        download_id=result.download_id,
        output_dir=_display_path(result.output_dir),
        files=[_display_path(file_path) for file_path in result.files],
        file_urls=_file_urls(result),
    )


@router.get("/{download_id}", response_model=PostDownloadResponse)
def get_download(request: Request, download_id: str) -> PostDownloadResponse:
    post_download_service = request.app.state.post_download_service
    result = post_download_service.get_result(download_id)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="download not found")

    return PostDownloadResponse(
        status="finished",
        download_id=result.download_id,
        output_dir=_display_path(result.output_dir),
        files=[_display_path(file_path) for file_path in result.files],
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
    shutil.rmtree(Path(entry.output_dir), ignore_errors=True)
    store.delete_entry(download_id)
    return {"deleted": True}


def _file_urls(result: PostDownloadResult) -> list[str]:
    return [f"/downloads/{result.download_id}/files/{index}" for index, _ in enumerate(result.files)]


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)
