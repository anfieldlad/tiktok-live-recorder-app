from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

try:
    import win32crypt  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - platform dependent
    win32crypt = None

try:
    from Cryptodome.Cipher import AES
except ImportError:  # pragma: no cover - optional until browser import is used
    AES = None


def is_browser_import_supported() -> bool:
    return os.name == "nt" and win32crypt is not None and AES is not None


def require_browser_import_support() -> None:
    if os.name != "nt":
        raise ValueError("browser cookie import is currently supported on Windows only")
    if win32crypt is None or AES is None:
        raise ValueError("browser cookie import dependencies are not installed")


def get_master_key(local_state_path: Path) -> bytes:
    require_browser_import_support()
    local_state = json.loads(local_state_path.read_text(encoding="utf-8"))
    encrypted_key_b64 = local_state["os_crypt"]["encrypted_key"]
    encrypted_key = base64.b64decode(encrypted_key_b64)
    encrypted_key = encrypted_key[5:]
    return win32crypt.CryptUnprotectData(encrypted_key, None, None, None, 0)[1]


def read_cookies_for_domain(cookies_db_path: Path, master_key: bytes, domain: str) -> dict[str, str]:
    temp_db_path = Path(tempfile.gettempdir()) / f"chromium_cookies_{os.getpid()}_{int(time.time() * 1000)}.sqlite"
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
            (f"%{domain}%",),
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
        decrypted = value or decrypt_cookie(encrypted_value, master_key)
        if decrypted:
            cookies[name] = decrypted
    return cookies


def decrypt_cookie(encrypted_value: bytes, master_key: bytes) -> str:
    require_browser_import_support()
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
