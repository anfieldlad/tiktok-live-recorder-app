from __future__ import annotations

import secrets
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.parse import urlparse

from curl_cffi import requests

from app.services.cookie_service import CookieService


@dataclass(frozen=True)
class PostDownloadResult:
    download_id: str
    output_dir: Path
    files: list[Path]


class PostDownloadService:
    def __init__(self, output_dir: Path, cookie_service: CookieService | None = None) -> None:
        self.output_dir = output_dir / "posts"
        self.cookie_service = cookie_service
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._results: dict[str, PostDownloadResult] = {}

    def validate_url(self, url: str) -> str:
        normalized = url.strip()
        parsed = urlparse(normalized)
        hostname = parsed.hostname or ""
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("download URL must start with http or https")
        if not hostname or not (hostname == "tiktok.com" or hostname.endswith(".tiktok.com")):
            raise ValueError("download URL must be a TikTok URL")
        return normalized

    def download(self, url: str) -> PostDownloadResult:
        normalized_url = self.validate_url(url)
        download_id = self._new_download_id()
        download_dir = self.output_dir / download_id
        download_dir.mkdir(parents=True, exist_ok=False)

        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--no-playlist",
            "--check-formats",
            "--rm-cache-dir",
            "--impersonate",
            "chrome",
            "--add-header",
            "Referer: https://www.tiktok.com/",
            "--write-info-json",
            "--paths",
            str(download_dir),
            "--output",
            "%(title).80s-%(id)s.%(ext)s",
            normalized_url,
        ]
        cookie_file = self._write_cookie_file()
        if cookie_file:
            command[3:3] = ["--cookies", str(cookie_file)]

        try:
            result = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=1200,
            )
        finally:
            if cookie_file:
                cookie_file.unlink(missing_ok=True)
        if result.returncode != 0:
            download_error = self._format_download_error(result.stderr)
            if "Unsupported URL" in download_error or "HTTP Error 403" in download_error:
                fallback_result = self._download_with_metadata_fallback(normalized_url, download_id, download_dir)
                if fallback_result is not None:
                    self._results[download_id] = fallback_result
                    return fallback_result
            raise RuntimeError(download_error)

        files = sorted(path for path in download_dir.rglob("*") if path.is_file())
        if not files:
            raise RuntimeError("download finished but no output files were created")

        download_result = PostDownloadResult(download_id=download_id, output_dir=download_dir, files=files)
        self._results[download_id] = download_result
        return download_result

    def get_result(self, download_id: str) -> PostDownloadResult | None:
        return self._results.get(download_id)

    def resolve_file(self, download_id: str, file_index: int) -> Path:
        result = self.get_result(download_id)
        if result is None:
            raise KeyError("download not found")
        try:
            file_path = result.files[file_index]
        except IndexError as exc:
            raise KeyError("download file not found") from exc

        resolved_file = file_path.resolve()
        resolved_output_dir = result.output_dir.resolve()
        if not resolved_file.is_file() or not resolved_file.is_relative_to(resolved_output_dir):
            raise FileNotFoundError("download file does not exist")
        return resolved_file

    def _download_with_metadata_fallback(self, url: str, download_id: str, download_dir: Path) -> PostDownloadResult | None:
        api_url = "https://www.tikwm.com/api/?" + urlencode({"url": url})
        response = requests.get(api_url, impersonate="chrome", timeout=30)
        if response.status_code != 200:
            return None

        payload = response.json()
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            return None

        metadata_path = download_dir / "metadata.json"
        metadata_path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        files = [metadata_path]
        image_urls = data.get("images")
        if isinstance(image_urls, list) and image_urls:
            for index, image_url in enumerate(image_urls, start=1):
                if not isinstance(image_url, str):
                    continue
                media_response = requests.get(
                    image_url,
                    impersonate="chrome",
                    headers={"Referer": "https://www.tiktok.com/"},
                    timeout=60,
                )
                media_response.raise_for_status()
                extension = self._media_extension(media_response.headers.get("content-type"), image_url, ".jpg")
                image_path = download_dir / f"image-{index:03d}{extension}"
                image_path.write_bytes(media_response.content)
                files.append(image_path)
        else:
            video_url = data.get("wmplay") or data.get("play")
            if not isinstance(video_url, str) or not video_url:
                return None
            media_response = requests.get(
                video_url,
                impersonate="chrome",
                headers={"Referer": "https://www.tiktok.com/"},
                timeout=120,
            )
            media_response.raise_for_status()
            extension = self._media_extension(media_response.headers.get("content-type"), video_url, ".mp4")
            video_path = download_dir / f"{data.get('id') or download_id}{extension}"
            video_path.write_bytes(media_response.content)
            files.append(video_path)

        if len(files) == 1:
            return None
        return PostDownloadResult(download_id=download_id, output_dir=download_dir, files=files)

    def _new_download_id(self) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        return f"{timestamp}-{secrets.token_hex(3)}"

    def _format_download_error(self, stderr: str) -> str:
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        if not lines:
            return "yt-dlp failed without an error message"
        return lines[-1]

    def _media_extension(self, content_type: str | None, media_url: str, default: str) -> str:
        content_type = (content_type or "").split(";", 1)[0].strip().lower()
        content_type_extensions = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "video/mp4": ".mp4",
            "audio/mpeg": ".mp3",
        }
        if content_type in content_type_extensions:
            return content_type_extensions[content_type]

        suffix = Path(urlparse(media_url).path).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".mp4", ".mp3"}:
            return ".jpg" if suffix == ".jpeg" else suffix
        return default

    def _write_cookie_file(self) -> Path | None:
        if self.cookie_service is None or not self.cookie_service.is_configured():
            return None

        cookies = self.cookie_service.read_cookies()
        normalized: dict[str, str] = {}
        for name, value in cookies.items():
            if not value:
                continue
            normalized[str(name)] = str(value)

        session_ss = normalized.get("session_ss")
        if session_ss:
            normalized.setdefault("sessionid_ss", session_ss)
            normalized.setdefault("sessionid", session_ss)

        if not normalized:
            return None

        cookie_file = Path(tempfile.gettempdir()) / f"tiktok-post-cookies-{secrets.token_hex(8)}.txt"
        lines = ["# Netscape HTTP Cookie File"]
        for name, value in sorted(normalized.items()):
            lines.append(f".tiktok.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}")
        cookie_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        cookie_file.chmod(0o600)
        return cookie_file
