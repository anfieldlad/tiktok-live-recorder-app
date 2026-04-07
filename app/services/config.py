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
    jobs_file: Path = PROJECT_ROOT / "data" / "jobs.json"
    output_dir: Path = PROJECT_ROOT / "output"
    logs_dir: Path = PROJECT_ROOT / "logs"
    recorder_dir: Path = PROJECT_ROOT / "vendor" / "tiktok-live-recorder"
    recorder_entrypoint: Path = PROJECT_ROOT / "vendor" / "tiktok-live-recorder" / "src" / "main.py"
    recorder_cookies_file: Path = PROJECT_ROOT / "vendor" / "tiktok-live-recorder" / "src" / ".." / "cookies.json"
    python_bin: str = "python"
    recorder_mode: str = Field(default="manual")
    recorder_proxy: str | None = None
    recorder_bitrate: str | None = None
    skip_update_check: bool = True
    cleanup_max_age_hours: int = 3

    def ensure_directories(self) -> None:
        self.jobs_file.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.recorder_cookies_file.parent.mkdir(parents=True, exist_ok=True)


def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_directories()
    return settings
