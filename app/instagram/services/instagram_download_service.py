from __future__ import annotations

import logging
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from app.instagram.services.instagram_cookie_service import InstagramCookieService
from app.models.download import DownloadEntry, DownloadPlatform, new_download_id
from app.services.download_store import DownloadStore


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InstagramDownloadResult:
    download_id: str
    output_dir: Path
    files: list[Path]


class InstagramDownloadService:
    def __init__(
        self,
        output_dir: Path,
        cookie_service: InstagramCookieService | None = None,
        download_store: DownloadStore | None = None,
    ) -> None:
        self.output_dir = output_dir / "instagram"
        self.cookie_service = cookie_service
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.download_store = download_store
        self._results: dict[str, InstagramDownloadResult] = {}

    def remember(self, result: InstagramDownloadResult) -> InstagramDownloadResult:
        """Record where a download's files landed.

        This merges rather than replaces: when a job service pre-allocated the
        entry, the lifecycle fields on it (status, url, started_at) belong to
        that service and must survive. Only the synchronous door reaches the
        fallback branch, and there a completed download is exactly what this is.
        """
        if self.download_store is None:
            self._results[result.download_id] = result
            return result

        updated = self.download_store.update_entry(
            result.download_id,
            lambda current: current.model_copy(
                update={
                    "output_dir": str(result.output_dir),
                    "files": [str(path) for path in result.files],
                }
            ),
        )
        if updated is None:
            self.download_store.save_entry(
                DownloadEntry(
                    id=result.download_id,
                    platform=DownloadPlatform.instagram,
                    output_dir=str(result.output_dir),
                    files=[str(path) for path in result.files],
                )
            )
        return result

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

    def download(self, url: str, download_id: str | None = None) -> InstagramDownloadResult:
        normalized_url = self.validate_url(url)
        download_id = download_id or new_download_id()
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

        return self.remember(
            InstagramDownloadResult(download_id=download_id, output_dir=download_dir, files=files)
        )

    def _engine_order(self, url: str):
        if self._is_reel(url):
            return [self._run_yt_dlp, self._run_gallery_dl]
        return [self._run_gallery_dl, self._run_yt_dlp]

    def _is_reel(self, url: str) -> bool:
        path = urlparse(url).path.lower()
        return "/reel/" in path or "/reels/" in path

    def get_result(self, download_id: str) -> InstagramDownloadResult | None:
        if self.download_store is None:
            return self._results.get(download_id)
        entry = self.download_store.get_entry(download_id)
        if entry is None:
            return None
        return InstagramDownloadResult(
            download_id=entry.id,
            output_dir=Path(entry.output_dir),
            files=[Path(path) for path in entry.files],
        )

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

    def _run_gallery_dl(self, url: str, download_dir: Path, cookie_file: Path | None) -> str | None:
        command = [
            sys.executable,
            "-m",
            "gallery_dl",
            "--retries",
            "2",
            "--dest",
            str(download_dir),
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
        # Only media is useful to the user — drop any metadata sidecars the
        # engines might still emit (gallery-dl/yt-dlp .json).
        return sorted(
            path
            for path in download_dir.rglob("*")
            if path.is_file() and path.suffix.lower() != ".json"
        )

    def create_archive(self, download_id: str) -> Path:
        """Zip every media file from a download into a single temp archive so
        the user can grab a whole post/carousel in one click."""
        result = self.get_result(download_id)
        if result is None:
            raise KeyError("download not found")

        resolved_output_dir = result.output_dir.resolve()
        media_files = [
            path
            for path in result.files
            if path.resolve().is_file() and path.resolve().is_relative_to(resolved_output_dir)
        ]
        if not media_files:
            raise FileNotFoundError("no files to archive")

        archive_path = Path(tempfile.gettempdir()) / f"instagram-{download_id}-{secrets.token_hex(4)}.zip"
        with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_STORED) as archive:
            for path in media_files:
                archive.write(path, arcname=path.name)
        return archive_path

    def _format_error(self, output: str, default: str) -> str:
        lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
        if not lines:
            return default
        return lines[-1]

    def _write_cookie_file(self) -> Path | None:
        if self.cookie_service is None:
            return None
        return self.cookie_service.write_netscape_cookie_file()
