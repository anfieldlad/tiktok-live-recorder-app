"""Resolve a live room's stream URL in-process.

Room lookup stays with the vendor recorder — turning a username or URL into a
room id needs its signed-URL logic. The room -> stream step does not, so it
happens here: one HTTP call, no subprocess hop, and no dependency on vendor
internals for the part the relay and the status check both need.

Historical note, since the comments here used to claim otherwise: an earlier
investigation blamed `status_code: 4003110` on TLS fingerprinting of the
vendor's HTTP client. That was wrong. The vendor already uses `curl_cffi`, and
its exact session config returns `status_code: 0` for the same room. The real
cause was that the app wrote sessions to a different file than the recorder
read (see the note on `recorder_cookies_file` in config.py), so the recorder
was unauthenticated and TikTok correctly refused age-gated rooms.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from curl_cffi import requests


logger = logging.getLogger(__name__)

WEBCAST_URL = "https://webcast.tiktok.com"
ROOM_STATUS_LIVE = 2

# TikTok's code for a room it will not hand over to this client.
STATUS_RESTRICTED = 4003110


class LiveStreamUnavailable(Exception):
    """Raised when the room exists but no stream can be pulled from it."""


def fetch_room_info(room_id: str, cookies: dict[str, str] | None, timeout: int = 30) -> dict[str, Any]:
    response = requests.get(
        f"{WEBCAST_URL}/webcast/room/info/?aid=1988&room_id={room_id}",
        cookies=cookies or {},
        impersonate="chrome",
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def is_room_live(payload: dict[str, Any]) -> bool:
    return (payload.get("data") or {}).get("status") == ROOM_STATUS_LIVE


def extract_live_url(payload: dict[str, Any]) -> str | None:
    """Pick the best FLV URL, mirroring the vendor's quality selection."""
    stream_url = ((payload.get("data") or {}).get("stream_url")) or {}

    pull_data = (stream_url.get("live_core_sdk_data") or {}).get("pull_data") or {}
    sdk_data_str = pull_data.get("stream_data")
    if not sdk_data_str:
        flv = stream_url.get("flv_pull_url") or {}
        return (
            flv.get("FULL_HD1")
            or flv.get("HD1")
            or flv.get("SD2")
            or flv.get("SD1")
            or stream_url.get("rtmp_pull_url")
            or None
        )

    try:
        sdk_data = json.loads(sdk_data_str).get("data", {})
    except (TypeError, ValueError):
        logger.warning("Could not parse live stream sdk data")
        return None

    qualities = (pull_data.get("options") or {}).get("qualities") or []
    level_by_key = {quality["sdk_key"]: quality["level"] for quality in qualities if "sdk_key" in quality}

    best_level = -1
    best_flv: str | None = None
    for sdk_key, entry in sdk_data.items():
        level = level_by_key.get(sdk_key, -1)
        if level > best_level:
            best_level = level
            best_flv = (entry.get("main") or {}).get("flv")
    return best_flv


def resolve_live_stream(room_id: str, cookies: dict[str, str] | None, timeout: int = 30) -> dict[str, Any]:
    """Return {is_live, live_url, message} for a room id.

    `message` is empty when the stream is usable, and explains the problem
    otherwise. `4003110` means TikTok will not hand this room to this session —
    genuinely age-gated, or signed in as an account that may not watch it.
    """
    payload = fetch_room_info(room_id, cookies, timeout=timeout)
    status_code = payload.get("status_code")

    if status_code == STATUS_RESTRICTED:
        return {
            "is_live": False,
            "live_url": None,
            "message": "TikTok has restricted this live for your account (often age-restricted/18+).",
        }
    if status_code not in (0, None):
        return {
            "is_live": False,
            "live_url": None,
            "message": f"TikTok refused the live stream request (status {status_code}).",
        }

    live = is_room_live(payload)
    live_url = extract_live_url(payload) if live else None
    if live and not live_url:
        return {"is_live": True, "live_url": None, "message": "The live was found, but no stream URL was offered."}
    if not live:
        return {"is_live": False, "live_url": None, "message": "This user is not live right now."}
    return {"is_live": True, "live_url": live_url, "message": ""}
