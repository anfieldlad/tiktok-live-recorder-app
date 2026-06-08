from __future__ import annotations

import json
import logging
import subprocess
from datetime import datetime
from typing import Iterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse


logger = logging.getLogger(__name__)

# New, self-contained router. It does not touch the existing recording/watch/download
# routes or services — the only wiring is a single include_router() in app/main.py.
router = APIRouter(prefix="/live", tags=["live-relay"])


# Resolves the live stream CDN URL for a username/url using the vendor recorder's
# TikTokAPI — exactly the path LiveStatusService uses, but it also returns the URL so we
# can relay it. Runs in the recorder's own venv/cwd so its imports resolve.
_RESOLVE_SCRIPT = """
import json
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath("src/main.py")))
sys.path.append(os.path.abspath("."))

from core.tiktok_api import TikTokAPI
from utils.utils import read_cookies

payload = json.loads(sys.argv[1])
api = TikTokAPI(proxy=None, cookies=read_cookies())
username = payload.get("username")
url = payload.get("url")

out = {"username": username, "room_id": None, "live_url": None, "error": None}
try:
    if url:
        username, room_id = api.get_room_and_user_from_url(url)
    else:
        signed_url = api._tikrec_get_room_id_signed_url(username)
        data = api.http_client.get(signed_url).json()
        if data.get("message") == "user_not_found":
            raise Exception("Username / RoomID not found or the user has never been in live.")
        room_id = ((data.get("data") or {}).get("user") or {}).get("roomId")

    out["username"] = username
    out["room_id"] = room_id
    if not room_id:
        raise Exception("No active room was found for this account.")
    if not api.is_room_alive(room_id):
        raise Exception("Account found, but it is not live right now.")

    live_url = api.get_live_url(room_id)
    if not live_url:
        raise Exception("Account looks live, but the stream URL is not available.")
    out["live_url"] = live_url
except Exception as exc:  # noqa: BLE001 - surface the message to the client
    out["error"] = str(exc)

print(json.dumps(out))
"""


def _resolve_live_url(settings, username: str | None, url: str | None) -> dict:
    completed = subprocess.run(
        [
            settings.python_bin,
            "-c",
            _RESOLVE_SCRIPT,
            json.dumps({"username": username, "url": url}),
        ],
        cwd=str(settings.recorder_dir),
        capture_output=True,
        text=True,
        check=False,
    )
    stdout = (completed.stdout or "").strip()
    if not stdout:
        return {"error": (completed.stderr or "").strip() or "live resolution returned no output"}
    last_line = [line for line in stdout.splitlines() if line.strip()][-1]
    try:
        return json.loads(last_line)
    except json.JSONDecodeError:
        return {"error": last_line}


def _ffmpeg_stream(ffmpeg_bin: str, live_url: str) -> Iterator[bytes]:
    """Pipe the live stream through ffmpeg as fragmented MP4 to stdout, copy codecs
    (no re-encode), and yield chunks. Nothing is written to disk on the server."""
    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel", "error",
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
        logger.info("Live relay ffmpeg process ended", extra={"returncode": process.poll()})


@router.get("/stream")
def stream_live(request: Request, username: str | None = None, url: str | None = None) -> StreamingResponse:
    """Relay a TikTok live stream straight to the client as MP4. The client decides
    where to store it; the server keeps no copy."""
    if not username and not url:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="username or url is required")

    settings = request.app.state.settings
    info = _resolve_live_url(settings, username, url)
    if info.get("error") or not info.get("live_url"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=info.get("error") or "the live stream is not available",
        )

    live_url = info["live_url"]
    name = (info.get("username") or username or "tiktok-live").lstrip("@")
    filename = f"{name}-{datetime.utcnow():%Y%m%d-%H%M%S}.mp4"
    logger.info("Starting live relay", extra={"username": name})

    return StreamingResponse(
        _ffmpeg_stream(settings.ffmpeg_bin, live_url),
        media_type="video/mp4",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
