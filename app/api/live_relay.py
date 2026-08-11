from __future__ import annotations

import logging
import subprocess
import threading
from datetime import datetime, timezone
from typing import Callable, Iterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.models.recording import RecordingCreateRequest, normalize_username
from app.services.url_guard import validate_tiktok_url


logger = logging.getLogger(__name__)

# New, self-contained router. It does not touch the existing recording/watch/download
# routes or services — the only wiring is a single include_router() in app/main.py.
router = APIRouter(prefix="/live", tags=["live-relay"])


def _ffmpeg_stream(ffmpeg_bin: str, live_url: str, release_slot: Callable[[], None]) -> Iterator[bytes]:
    """Pipe the live stream through ffmpeg as fragmented MP4 to stdout, copy codecs
    (no re-encode), and yield chunks. Nothing is written to disk on the server."""
    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
        # TikTok's CDN rotates edges and closes the FLV connection every few seconds;
        # without reconnect, ffmpeg treats that EOF as the end and the recording stops
        # after ~20s. Reconnect to the same URL on drop/EOF (mirrors the recorder's loop).
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5",
        # A live FLV is joined mid-stream, so the initial codec header (SPS/PPS) was
        # already sent. Probe longer to catch an in-band keyframe before writing the
        # MP4 header, regenerate timestamps, and drop the corrupt leading packets.
        "-analyzeduration", "10M",
        "-probesize", "10M",
        "-fflags", "+genpts+discardcorrupt",
        "-i", live_url,
        "-c", "copy",
        "-movflags", "frag_keyframe+empty_moov+default_base_moof",
        "-f", "mp4",
        "pipe:1",
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert process.stdout is not None
        while True:
            chunk = process.stdout.read(65536)
            if not chunk:
                break
            yield chunk
    finally:
        # Client disconnected or stream ended — make sure ffmpeg is gone.
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        if process.stdout is not None:
            process.stdout.close()
        release_slot()
        logger.info("Live relay ffmpeg process ended", extra={"returncode": process.poll()})


_relay_slots_lock = threading.Lock()


def _relay_slots(request: Request) -> threading.BoundedSemaphore:
    state = request.app.state
    with _relay_slots_lock:
        slots = getattr(state, "live_relay_slots", None)
        if slots is None:
            slots = threading.BoundedSemaphore(max(1, state.settings.max_concurrent_live_relays))
            state.live_relay_slots = slots
        return slots


def _safe_name(value: str | None, fallback: str) -> str:
    """Usernames land in a Content-Disposition header, so keep them boring."""
    try:
        return normalize_username(value) or fallback
    except ValueError:
        return fallback


@router.get("/stream")
def stream_live(request: Request, username: str | None = None, url: str | None = None) -> StreamingResponse:
    """Relay a TikTok live stream straight to the client as MP4. The client decides
    where to store it; the server keeps no copy."""
    if not username and not url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="username or url is required")

    try:
        username = normalize_username(username)
        if url:
            url = validate_tiktok_url(url, label="url")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if not username and not url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="username or url is required")

    settings = request.app.state.settings
    # Every relay holds an ffmpeg process for as long as the client keeps reading,
    # so cap how many can run at once instead of letting callers fork the box.
    slots = _relay_slots(request)
    if not slots.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many live relays are running right now, try again shortly",
        )

    try:
        # Shares the resolver with check-live, so the relay and the status the
        # user was just shown cannot disagree.
        live_status_service = request.app.state.live_status_service
        info = live_status_service.resolve_stream_url(
            RecordingCreateRequest(username=username, url=url)
        )
        if info.get("error") or not info.get("live_url"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=info.get("error") or "the live stream is not available",
            )

        live_url = info["live_url"]
        name = _safe_name(info.get("username"), username or "tiktok-live")
        filename = f"{name}-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.mp4"
        logger.info("Starting live relay", extra={"username": name})

        # The slot is handed to the generator, which releases it when ffmpeg ends.
        return StreamingResponse(
            _ffmpeg_stream(settings.ffmpeg_bin, live_url, slots.release),
            media_type="video/mp4",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except BaseException:
        slots.release()
        raise
