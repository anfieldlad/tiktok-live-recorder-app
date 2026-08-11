from __future__ import annotations

import json
import subprocess

from app.models.recording import LiveStatusResponse, RecordingCreateRequest
from app.services.config import Settings
from app.services.redaction import redact_sensitive


class LiveStatusService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def check(self, payload: RecordingCreateRequest) -> LiveStatusResponse:
        script = """
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

result = {
    "username": username,
    "url": url,
    "room_id": None,
    "is_live": False,
    "can_record": False,
    "message": "",
}

try:
    if url:
        username, room_id = api.get_room_and_user_from_url(url)
    else:
        signed_url = api._tikrec_get_room_id_signed_url(username)
        response = api.http_client.get(signed_url)
        data = response.json()
        if data.get("message") == "user_not_found":
            raise Exception("Username / RoomID not found or the user has never been in live.")
        room_id = ((data.get("data") or {}).get("user") or {}).get("roomId")

    result["username"] = username
    result["room_id"] = room_id

    if not room_id:
        result["message"] = "No active room was found for this account."
    else:
        is_live = api.is_room_alive(room_id)
        result["is_live"] = bool(is_live)
        if not is_live:
            result["message"] = "Account found, but it is not live right now."
        else:
            live_url = api.get_live_url(room_id)
            if live_url:
                result["can_record"] = True
                result["message"] = "Account is live and the stream URL is available."
            else:
                result["message"] = "Account looks live, but the stream URL is not available."
except Exception as exc:
    result["message"] = str(exc)

print(json.dumps(result))
"""
        try:
            completed = subprocess.run(
                [
                    self.settings.python_bin,
                    "-c",
                    script,
                    json.dumps(
                        {
                            "username": payload.username,
                            "url": str(payload.url) if payload.url else None,
                        }
                    ),
                ],
                cwd=str(self.settings.recorder_dir),
                capture_output=True,
                text=True,
                check=False,
                # A hung check would otherwise hold a request worker — and the watch
                # loop thread — open forever.
                timeout=self.settings.live_resolve_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("the live status check timed out") from exc

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if not stdout:
            raise RuntimeError(redact_sensitive(stderr) or "live status check returned no output")

        lines = [line for line in stdout.splitlines() if line.strip()]
        data = json.loads(lines[-1])
        if stderr and not data.get("message"):
            data["message"] = redact_sensitive(stderr)
        data["message"] = self._normalize_message(data.get("message", ""))
        return LiveStatusResponse.model_validate(data)

    def _normalize_message(self, message: str) -> str:
        normalized = (message or "").strip()
        mapping = {
            "Account is private, login required.": "This account is private. Sign in to TikTok to access this live.",
            "This account is private. Follow the creator to access their LIVE.": "This account is private. Your TikTok account must follow this creator to view the live.",
            "Live is private, login required.": "TikTok has restricted this live (often age-restricted/18+). Recording needs a signed-in, age-verified TikTok session, and some restricted lives can't be accessed at all.",
            "Username / RoomID not found or the user has never been in live.": "User ID not found.",
            "The user has never hosted a live stream on TikTok.": "This account has never gone live on TikTok.",
            "The user is not hosting a live stream at the moment.": "This user is not live right now.",
            "Unable to retrieve live streaming url. Please try again later.": "The live was detected, but the stream could not be accessed right now.",
            "The provided URL is not a valid TikTok live stream.": "The TikTok live URL is invalid.",
            "Error extracting RoomID": "We couldn't resolve this user's live room.",
            "Your IP is blocked by TikTok WAF. Please change your IP address.": "TikTok is blocking requests from this IP address right now.",
            "No active room was found for this account.": "This user is not live right now.",
            "Account found, but it is not live right now.": "This user is not live right now.",
            "Account is live and the stream URL is available.": "This user is live and ready to record.",
            "Account looks live, but the stream URL is not available.": "This user appears to be live, but the stream is not accessible for recording.",
        }
        for key, value in mapping.items():
            if normalized.startswith(key):
                return value
        return normalized or "We couldn't determine the live status right now."
