from __future__ import annotations

import json
import os
from pathlib import Path

from app.services.chromium_cookies import (
    get_master_key,
    read_cookies_for_domain,
    require_browser_import_support,
)
from app.services.secure_files import write_private_temp_text, write_private_text


class InstagramCookieService:
    def __init__(self, cookie_file: Path) -> None:
        self.cookie_file = cookie_file.resolve()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self.cookie_file.exists():
            write_private_text(self.cookie_file, "{}\n")

    def is_configured(self) -> bool:
        data = self.read_cookies()
        return bool(data.get("sessionid"))

    def read_cookies(self) -> dict:
        self._ensure_file()
        raw = self.cookie_file.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        return json.loads(raw)

    def save_session_cookie(self, sessionid: str) -> None:
        payload = {"sessionid": sessionid}
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
        cookies = read_cookies_for_domain(cookies_db_path, master_key, "instagram.com")
        if not cookies:
            raise ValueError(f"no Instagram cookies found in {browser_name}")
        self.save_cookie_map(cookies)
        return cookies

    def import_from_browser_profile(self, local_state_path: Path, cookies_db_path: Path) -> dict:
        require_browser_import_support()
        if not local_state_path.exists():
            raise ValueError("browser Local State file was not found")
        if not cookies_db_path.exists():
            raise ValueError("browser Cookies database was not found")
        master_key = get_master_key(local_state_path)
        cookies = read_cookies_for_domain(cookies_db_path, master_key, "instagram.com")
        if not cookies:
            raise ValueError("no Instagram cookies found in the selected browser profile")
        self.save_cookie_map(cookies)
        return cookies

    def clear(self) -> None:
        write_private_text(self.cookie_file, "{}\n")

    def write_netscape_cookie_file(self) -> Path | None:
        if not self.is_configured():
            return None

        cookies = self.read_cookies()
        normalized: dict[str, str] = {}
        for name, value in cookies.items():
            if not value:
                continue
            normalized[str(name)] = str(value)

        if not normalized:
            return None

        lines = ["# Netscape HTTP Cookie File"]
        for name, value in sorted(normalized.items()):
            lines.append(f".instagram.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}")
        return write_private_temp_text("\n".join(lines) + "\n", prefix="instagram-cookies-")
