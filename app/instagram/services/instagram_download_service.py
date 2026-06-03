from __future__ import annotations

import logging
import secrets
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from app.instagram.services.instagram_cookie_service import InstagramCookieService


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstagramDownloadResult:
    download_id: str
    output_dir: Path
    files: list[Path]


class InstagramDownloadService:
    def __init__(self, output_dir: Path, cookie_service: InstagramCookieService | None = None) -> None:
        self.output_dir = output_dir / "instagram"
        self.cookie_service = cookie_service
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._results: dict[str, InstagramDownloadResult] = {}

    def validate_url(self, url: str) -> str:
        normalized = url.strip()
        parsed = urlparse(normalized)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("download URL must start with http or https")
        allowed = hostname in {"instagram.com", "www.instagram.com", "instagr.am"} or hostname.endswith(".instagram.com")
        if not allowed:
            raise ValueError("download URL must be an Instagram URL")
        return normalized

    def download(self, url: str) -> InstagramDownloadResult:
        normalized_url = self.validate_url(url)
        download_id = self._new_download_id()
        download_dir = self.output_dir / download_id
        download_dir.mkdir(parents=True, exist_ok=False)

        cookie_file = self._write_cookie_file()
        try:
            # Run the best engine for this URL first, then fall back to the other.
            # Reels are single videos where yt-dlp is strongest; posts, carousels,
            # stories, and highlights are gallery-dl's strength. Picking the right
            # primary avoids burning minutes on a doomed first attempt.
            files: list[Path] = []
            errors: list[str] = []
            for engine in self._engine_order(normalized_url):
                error = engine(normalized_url, download_dir, cookie_file)
                files = self._collect_files(download_dir)
                if files:
                    break
                if error:
                    errors.append(error)
            if not files:
                raise RuntimeError(errors[0] if errors else "download finished but no output files were created")
        finally:
            if cookie_file:
                cookie_file.unlink(missing_ok=True)

        download_result = InstagramDownloadResult(download_id=download_id, output_dir=download_dir, files=files)
        self._results[download_id] = download_result
        return download_result

    def _engine_order(self, url: str):
        if self._is_reel(url):
            return [self._run_yt_dlp, self._run_gallery_dl]
        return [self._run_gallery_dl, self._run_yt_dlp]

    def _is_reel(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return "/reel/" in path or "/reels/" in path

    def get_result(self, download_id: str) -> InstagramDownloadResult | None:
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

    def cleanup_file_after_download(self, download_id: str, file_index: int) -> None:
        """Delete a downloaded file after it is served, and wipe the whole
        download once all media is gone (mirrors the TikTok recording flow).
        Leftover metadata (.json) is swept along with the last media file."""
        result = self._results.get(download_id)
        if result is None:
            return
        try:
            file_path = result.files[file_index]
        except IndexError:
            return

        resolved_output_dir = result.output_dir.resolve()
        try:
            resolved_file = file_path.resolve()
            if resolved_file.is_file() and resolved_file.is_relative_to(resolved_output_dir):
                resolved_file.unlink(missing_ok=True)
                logger.info("Deleted Instagram file after download", extra={"download_id": download_id, "file_index": file_index})
        finally:
            remaining = [path for path in result.output_dir.rglob("*") if path.is_file()]
            media_remaining = [path for path in remaining if path.suffix.lower() != ".json"]
            if not media_remaining:
                shutil.rmtree(result.output_dir, ignore_errors=True)
                self._results.pop(download_id, None)
                logger.info("Wiped Instagram download after all media downloaded", extra={"download_id": download_id})

    def _run_gallery_dl(self, url: str, download_dir: Path, cookie_file: Path | None) -> str | None:
        command = [
            sys.executable,
            "-m",
            "gallery_dl",
            "--retries",
            "2",
            "--dest",
            str(download_dir),
            "--write-metadata",
            url,
        ]
        if cookie_file:
            command[3:3] = ["--cookies", str(cookie_file)]

        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1200,
        )
        if result.returncode != 0:
            return self._format_error(result.stderr or result.stdout, "gallery-dl failed without an error message")
        return None

    def _run_yt_dlp(self, url: str, download_dir: Path, cookie_file: Path | None) -> str | None:
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--check-formats",
            "--rm-cache-dir",
            "--impersonate",
            "chrome",
            "--add-header",
            "Referer: https://www.instagram.com/",
            "--write-info-json",
            "--paths",
            str(download_dir),
            "--output",
            "%(title).80s-%(id)s.%(ext)s",
            url,
        ]
        if cookie_file:
            command[3:3] = ["--cookies", str(cookie_file)]

        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=1200,
        )
        if result.returncode != 0:
            return self._format_error(result.stderr or result.stdout, "yt-dlp failed without an error message")
        return None

    def _collect_files(self, download_dir: Path) -> list[Path]:
        return sorted(path for path in download_dir.rglob("*") if path.is_file())

    def _new_download_id(self) -> str:
        timestamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
        return f"{timestamp}-{secrets.token_hex(3)}"

    def _format_error(self, output: str, default: str) -> str:
        lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
        if not lines:
            return default
        return lines[-1]

    def _write_cookie_file(self) -> Path | None:
        if self.cookie_service is None:
            return None
        return self.cookie_service.write_netscape_cookie_file()
