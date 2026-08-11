from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    host: str = "0.0.0.0"
    port: int = 8000
    root_path: str = ""
    jobs_file: Path = PROJECT_ROOT / "data" / "jobs.json"
    watch_jobs_file: Path = PROJECT_ROOT / "data" / "watch_jobs.json"
    output_dir: Path = PROJECT_ROOT / "output"
    logs_dir: Path = PROJECT_ROOT / "logs"
    recorder_dir: Path = PROJECT_ROOT / "vendor" / "tiktok-live-recorder"
    recorder_entrypoint: Path = PROJECT_ROOT / "vendor" / "tiktok-live-recorder" / "src" / "main.py"
    # The recorder reads dirname(src/utils/utils.py)/../cookies.json, i.e.
    # src/cookies.json. Writing to the vendor root instead (which is what
    # "src/.." resolves to) left the recorder on a stale file with no sessionid,
    # so every age-gated live came back as "Live is private, login required".
    recorder_cookies_file: Path = PROJECT_ROOT / "vendor" / "tiktok-live-recorder" / "src" / "cookies.json"
    instagram_cookies_file: Path = PROJECT_ROOT / "data" / "instagram_cookies.json"
    python_bin: str = "python"
    recorder_mode: str = Field(default="manual")
    recorder_proxy: str | None = None
    recorder_bitrate: str | None = None
    ffmpeg_bin: str = "ffmpeg"
    skip_update_check: bool = True
    cleanup_max_age_hours: int = 3
    watch_poll_interval_seconds: int = 45
    process_stop_grace_seconds: int = 12
    live_resolve_timeout_seconds: int = 90
    max_concurrent_live_relays: int = 3

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() == "production"

    def ensure_directories(self) -> None:
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.watch_jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.recorder_cookies_file.parent.mkdir(parents=True, exist_ok=True)
        self.instagram_cookies_file.parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
