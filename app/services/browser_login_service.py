from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from app.services.cookie_service import CookieService


class BrowserLoginService:
    def __init__(self, project_root: Path, cookie_service: CookieService) -> None:
        self.cookie_service = cookie_service
        self.session_root = project_root / "data" / "browser-login"
        self.session_root.mkdir(parents=True, exist_ok=True)
        self.state_file = self.session_root / "state.json"
        self._ensure_state()

    def start_login(self, browser_name: str) -> dict:
        normalized = browser_name.lower()
        browser_exe = self._resolve_browser_path(normalized)
        profile_dir = self.session_root / normalized
        profile_dir.mkdir(parents=True, exist_ok=True)

        args = [
            str(browser_exe),
            f"--user-data-dir={profile_dir}",
            "--new-window",
            "https://www.tiktok.com/login",
        ]
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self._write_state(
            {
                "browser_name": normalized,
                "profile_dir": str(profile_dir),
                "browser_open": True,
                "browser_pid": process.pid,
                "authenticated": False,
            }
        )
        return self.status()

    def capture_session(self) -> dict:
        state = self._read_state()
        browser_name = state.get("browser_name")
        profile_dir_raw = state.get("profile_dir")
        if not browser_name or not profile_dir_raw:
            raise ValueError("no active login browser session")

        profile_dir = Path(profile_dir_raw)
        local_state_path = profile_dir / "Local State"
        cookies_db_path = profile_dir / "Default" / "Network" / "Cookies"

        if not local_state_path.exists() or not cookies_db_path.exists():
            raise ValueError("login session files are not ready yet. Please finish logging in and fully close the login browser")

        cookies = self.cookie_service.import_from_browser_profile(local_state_path, cookies_db_path)
        if "session_ss" not in cookies:
            raise ValueError("session_ss was not found yet. Please finish logging in on TikTok and close the login browser first")

        updated = {
            "browser_name": browser_name,
            "profile_dir": str(profile_dir),
            "browser_open": False,
            "browser_pid": None,
            "authenticated": True,
        }
        self._write_state(updated)
        return self.status()

    def close(self) -> dict:
        state = self._read_state()
        browser_name = state.get("browser_name")
        if browser_name:
            process_name = "chrome.exe" if browser_name == "chrome" else "msedge.exe"
            subprocess.run(
                ["taskkill", "/IM", process_name, "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        self._write_state(
            {
                "browser_name": None,
                "profile_dir": None,
                "browser_open": False,
                "browser_pid": None,
                "authenticated": False,
            }
        )
        return self.status()

    def status(self) -> dict:
        state = self._normalize_state(self._read_state())
        return {
            "browser_open": bool(state.get("browser_open")),
            "browser_name": state.get("browser_name"),
            "authenticated": bool(state.get("authenticated")),
            "cookies_configured": self.cookie_service.is_configured(),
        }

    def _resolve_browser_path(self, browser_name: str) -> Path:
        browser_paths = {
            "chrome": [
                Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
                Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            ],
            "edge": [
                Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
                Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
            ],
        }
        for candidate in browser_paths.get(browser_name, []):
            if candidate.exists():
                return candidate
        raise ValueError(f"{browser_name} browser executable was not found")

    def _ensure_state(self) -> None:
        if not self.state_file.exists():
            self._write_state(
                {
                    "browser_name": None,
                    "profile_dir": None,
                    "browser_open": False,
                    "browser_pid": None,
                    "authenticated": False,
                }
            )

    def _read_state(self) -> dict:
        self._ensure_state()
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def _normalize_state(self, state: dict) -> dict:
        browser_open = bool(state.get("browser_open"))
        browser_pid = state.get("browser_pid")
        if browser_open and (not browser_pid or not self._is_process_running(int(browser_pid))):
            state = {
                **state,
                "browser_open": False,
                "browser_pid": None,
            }
            self._write_state(state)
        return state

    def _is_process_running(self, pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _write_state(self, payload: dict) -> None:
        self.state_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
