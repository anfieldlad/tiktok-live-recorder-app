from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

from app.instagram.services.instagram_download_service import InstagramDownloadResult
from app.services.config import PROJECT_ROOT


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


@router.post("", response_model=InstagramDownloadResponse, status_code=status.HTTP_201_CREATED)
def create_download(request: Request, payload: InstagramDownloadCreateRequest) -> InstagramDownloadResponse:
    instagram_download_service = request.app.state.instagram_download_service
    try:
        result = instagram_download_service.download(payload.url)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    return _to_response(result)


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
        background=BackgroundTask(instagram_download_service.cleanup_file_after_download, download_id, file_index),
    )


def _to_response(result: InstagramDownloadResult) -> InstagramDownloadResponse:
    return InstagramDownloadResponse(
        status="finished",
        download_id=result.download_id,
        output_dir=_display_path(result.output_dir),
        files=[_display_path(file_path) for file_path in result.files],
        file_urls=[f"/instagram/downloads/{result.download_id}/files/{index}" for index, _ in enumerate(result.files)],
    )


def _display_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(resolved)
