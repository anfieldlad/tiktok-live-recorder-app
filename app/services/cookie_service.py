from __future__ import annotations

import json
import os
from pathlib import Path

from app.services.chromium_cookies import (
    get_master_key,
    read_cookies_for_domain,
    require_browser_import_support,
)
from app.services.secure_files import write_private_text


# TikTok authenticates on sessionid/sessionid_ss; session_ss is kept for
# backwards compatibility with cookie files this app wrote earlier.
SESSION_COOKIE_NAMES = ("sessionid", "sessionid_ss", "session_ss")


class CookieService:
    def __init__(self, cookie_file: Path) -> None:
        self.cookie_file = cookie_file.resolve()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self.cookie_file.exists():
            write_private_text(self.cookie_file, "{}\n")

    def is_configured(self) -> bool:
        data = self.read_cookies()
        return any(bool(data.get(name)) for name in SESSION_COOKIE_NAMES)

    def read_cookies(self) -> dict:
        self._ensure_file()
        raw = self.cookie_file.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        return json.loads(raw)

    def save_session_cookie(self, session_value: str) -> None:
        """Store one session value under every name TikTok might look for.

        The recorder writes these keys straight out as cookie names, and TikTok
        authenticates on `sessionid`/`sessionid_ss` — it has no `session_ss`
        cookie at all. Writing only `session_ss` left the recorder effectively
        anonymous, so age-gated rooms came back as "Live is private, login
        required" even for an account that could watch them in a browser.
        """
        payload = {name: session_value for name in SESSION_COOKIE_NAMES}
        write_private_text(self.cookie_file, json.dumps(payload, indent=2) + "\n")

    def save_cookie_map(self, cookies: dict[str, str]) -> None:
        write_private_text(self.cookie_file, json.dumps(cookies, indent=2) + "\n")

    def import_from_browser(self, browser_name: str) -> dict:
        require_browser_import_support()
        browser_paths = {
            "chrome": (
                Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data" / "Local State",
                Path(os.environ["LOCALAPPDATA"]) / "Google" / "Chrome" / "User Data" / "Default" / "Network" / "Cookies",
            ),
            "edge": (
                Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data" / "Local State",
                Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Edge" / "User Data" / "Default" / "Network" / "Cookies",
            ),
        }
        paths = browser_paths.get(browser_name.lower())
        if paths is None:
            raise ValueError("unsupported browser")
        local_state_path, cookies_db_path = paths
        if not local_state_path.exists():
            raise ValueError(f"{browser_name} Local State file was not found")
        if not cookies_db_path.exists():
            raise ValueError(f"{browser_name} Cookies database was not found")

        master_key = get_master_key(local_state_path)
        cookies = read_cookies_for_domain(cookies_db_path, master_key, "tiktok.com")
        if not cookies:
            raise ValueError(f"no TikTok cookies found in {browser_name}")
        self.save_cookie_map(cookies)
        return cookies

    def import_from_browser_profile(self, local_state_path: Path, cookies_db_path: Path) -> dict:
        require_browser_import_support()
        if not local_state_path.exists():
            raise ValueError("browser Local State file was not found")
        if not cookies_db_path.exists():
            raise ValueError("browser Cookies database was not found")
        master_key = get_master_key(local_state_path)
        cookies = read_cookies_for_domain(cookies_db_path, master_key, "tiktok.com")
        if not cookies:
            raise ValueError("no TikTok cookies found in the selected browser profile")
        self.save_cookie_map(cookies)
        return cookies

    def clear(self) -> None:
        write_private_text(self.cookie_file, "{}\n")
