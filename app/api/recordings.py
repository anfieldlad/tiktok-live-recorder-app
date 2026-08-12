from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from app.models.recording import (
    LiveStatusResponse,
    RecordingActionResponse,
    RecordingCreateRequest,
    RecordingCreateResponse,
    RecordingJobResponse,
    WatchCreateRequest,
    WatchJobResponse,
)


router = APIRouter(prefix="/recordings", tags=["recordings"])
watch_router = APIRouter(prefix="/watch-recordings", tags=["watch-recordings"])


@router.post("", response_model=RecordingCreateResponse, status_code=status.HTTP_201_CREATED)
def create_recording(request: Request, payload: RecordingCreateRequest) -> RecordingCreateResponse:
    recorder_service = request.app.state.recorder_service
    live_status_service = request.app.state.live_status_service
    try:
        live_status = live_status_service.check(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not live_status.can_record:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=live_status.message)
    try:
        job = recorder_service.create_job(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RecordingCreateResponse(id=job.id, status=job.status)


@router.post("/check-live", response_model=LiveStatusResponse)
def check_live_status(request: Request, payload: RecordingCreateRequest) -> LiveStatusResponse:
    live_status_service = request.app.state.live_status_service
    try:
        return live_status_service.check(payload)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("", response_model=list[RecordingJobResponse])
def list_recordings(request: Request) -> list[RecordingJobResponse]:
    job_store = request.app.state.job_store
    return [RecordingJobResponse.from_job(job) for job in job_store.list_jobs()]


@router.get("/{job_id}", response_model=RecordingJobResponse)
def get_recording(request: Request, job_id: str) -> RecordingJobResponse:
    job_store = request.app.state.job_store
    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    return RecordingJobResponse.from_job(job)


@router.post("/{job_id}/stop", response_model=RecordingActionResponse)
def stop_recording(request: Request, job_id: str) -> RecordingActionResponse:
    recorder_service = request.app.state.recorder_service
    try:
        job = recorder_service.stop_job(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return RecordingActionResponse(id=job.id, status=job.status, file_path=job.file_path, error=job.error)


@router.get("/{job_id}/download")
def download_recording(request: Request, job_id: str) -> FileResponse:
    job_store = request.app.state.job_store
    file_service = request.app.state.file_service

    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
    if not job.is_downloadable():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="job is not ready for download")

    try:
        file_path = file_service.resolve_job_file(job)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return FileResponse(
        path=file_path,
        filename=job.file_name,
        media_type="application/octet-stream",
        background=BackgroundTask(file_service.mark_downloaded, job_id),
    )


@router.delete("/{job_id}", response_model=RecordingActionResponse)
def delete_recording(request: Request, job_id: str) -> RecordingActionResponse:
    job_store = request.app.state.job_store
    recorder_service = request.app.state.recorder_service

    job = job_store.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")

    recorder_service.delete_job(job_id)
    return RecordingActionResponse(id=job.id, status=job.status, file_path=job.file_path, error=job.error)


@watch_router.post("", response_model=WatchJobResponse, status_code=status.HTTP_201_CREATED)
def create_watch_recording(request: Request, payload: WatchCreateRequest) -> WatchJobResponse:
    watch_service = request.app.state.watch_service
    try:
        job = watch_service.create_watch(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return WatchJobResponse.from_job(job)


@watch_router.get("", response_model=list[WatchJobResponse])
def list_watch_recordings(request: Request) -> list[WatchJobResponse]:
    watch_store = request.app.state.watch_store
    return [WatchJobResponse.from_job(job) for job in watch_store.list_jobs()]


@watch_router.post("/{watch_id}/stop", response_model=WatchJobResponse)
def stop_watch_recording(request: Request, watch_id: str) -> WatchJobResponse:
    watch_service = request.app.state.watch_service
    try:
        job = watch_service.stop_watch(watch_id)
    except KeyError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="watch job not found") from exc
    return WatchJobResponse.from_job(job)


@watch_router.delete("/{watch_id}", response_model=WatchJobResponse)
def delete_watch_recording(request: Request, watch_id: str) -> WatchJobResponse:
    watch_store = request.app.state.watch_store
    watch_service = request.app.state.watch_service
    job = watch_store.get_job(watch_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="watch job not found")
    watch_service.delete_watch(watch_id)
    return WatchJobResponse.from_job(job)
