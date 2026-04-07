from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

import win32crypt
from Cryptodome.Cipher import AES


class CookieService:
    def __init__(self, cookie_file: Path) -> None:
        self.cookie_file = cookie_file.resolve()
        self._ensure_file()

    def _ensure_file(self) -> None:
        if not self.cookie_file.exists():
            self.cookie_file.write_text("{}\n", encoding="utf-8")

    def is_configured(self) -> bool:
        data = self.read_cookies()
        return bool(data.get("session_ss"))

    def read_cookies(self) -> dict:
        self._ensure_file()
        raw = self.cookie_file.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        return json.loads(raw)

    def save_session_cookie(self, session_ss: str) -> None:
        payload = {"session_ss": session_ss}
        self.cookie_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def save_cookie_map(self, cookies: dict[str, str]) -> None:
        self.cookie_file.write_text(json.dumps(cookies, indent=2) + "\n", encoding="utf-8")

    def import_from_browser(self, browser_name: str) -> dict:
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

        master_key = self._get_chromium_master_key(local_state_path)
        cookies = self._read_tiktok_cookies(cookies_db_path, master_key)
        if not cookies:
            raise ValueError(f"no TikTok cookies found in {browser_name}")
        self.save_cookie_map(cookies)
        return cookies

    def import_from_browser_profile(self, local_state_path: Path, cookies_db_path: Path) -> dict:
        if not local_state_path.exists():
            raise ValueError("browser Local State file was not found")
        if not cookies_db_path.exists():
            raise ValueError("browser Cookies database was not found")
        master_key = self._get_chromium_master_key(local_state_path)
        cookies = self._read_tiktok_cookies(cookies_db_path, master_key)
        if not cookies:
            raise ValueError("no TikTok cookies found in the selected browser profile")
        self.save_cookie_map(cookies)
        return cookies

    def clear(self) -> None:
        self.cookie_file.write_text("{}\n", encoding="utf-8")

    def _get_chromium_master_key(self, local_state_path: Path) -> bytes:
        local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
        encrypted_key_b64 = local_state["os_crypt"]["encrypted_key"]
        encrypted_key = base64.b64decode(encrypted_key_b64)
        encrypted_key = encrypted_key[5:]
        return win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]

    def _read_tiktok_cookies(self, cookies_db_path: Path, master_key: bytes) -> dict[str, str]:
        temp_db_path = Path(tempfile.gettempdir()) / f"tiktok_cookies_{os.getpid()}_{int(time.time() * 1000)}.sqlite"
        shutil.copy2(cookies_db_path, temp_db_path)
        rows: list[tuple[str, bytes, str]] = []
        conn: sqlite3.Connection | None = None
        try:
            conn = sqlite3.connect(temp_db_path)
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT name, encrypted_value, value
                FROM cookies
                WHERE host_key LIKE ?
                """,
                ("%tiktok.com%",),
            )
            rows = cursor.fetchall()
            cursor.close()
        finally:
            if conn is not None:
                conn.close()
            for _ in range(5):
                try:
                    temp_db_path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.2)

        cookies: dict[str, str] = {}
        for name, encrypted_value, value in rows:
            decrypted = value or self._decrypt_chromium_cookie(encrypted_value, master_key)
            if decrypted:
                cookies[name] = decrypted
        return cookies

    def _decrypt_chromium_cookie(self, encrypted_value: bytes, master_key: bytes) -> str:
        if not encrypted_value:
            return ""
        if encrypted_value.startswith(b"v10") or encrypted_value.startswith(b"v11"):
            nonce = encrypted_value[3:15]
            ciphertext = encrypted_value[15:-16]
            tag = encrypted_value[-16:]
            cipher = AES.new(master_key, AES.MODE_GCM, nonce=nonce)
            decrypted = cipher.decrypt_and_verify(ciphertext, tag)
            return decrypted.decode("utf-8")
        decrypted = win32crypt.CryptUnprotectData(encrypted_value, None, None, None, 0)[1]
        return decrypted.decode("utf-8")
