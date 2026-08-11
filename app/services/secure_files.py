"""Helpers for files that hold session material.

Writing with `Path.write_text` and then `chmod(0o600)` leaves a window where the
file sits in the filesystem at umask permissions — on a shared host that is long
enough for another user to read a session cookie. These helpers set the mode at
creation time instead.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


PRIVATE_MODE = 0o600


def write_private_text(path: Path, content: str) -> None:
    """Create or replace `path` with `content`, readable only by this user."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    # O_CREAT only applies the mode to a file it just created; an existing file
    # keeps whatever mode it already had.
    _chmod_private(path)


def write_private_temp_text(content: str, prefix: str, suffix: str = ".txt") -> Path:
    """Write `content` to a fresh temp file created with 0600 from the start."""
    descriptor, name = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    path = Path(name)
    _chmod_private(path)
    return path


def _chmod_private(path: Path) -> None:
    try:
        os.chmod(path, PRIVATE_MODE)
    except OSError:  # pragma: no cover - best effort on non-POSIX filesystems
        pass
