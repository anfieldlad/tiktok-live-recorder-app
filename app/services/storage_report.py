"""What the output directory is costing, for the health endpoint and the UI.

Reporting only. Nothing here deletes: disk pressure is surfaced so the owner
can decide, because the alternative — a sweep that frees space on its own —
is how media gets destroyed without being seen.
"""

from __future__ import annotations

import shutil
from pathlib import Path


def storage_report(output_dir: Path, soft_limit_bytes: int) -> dict[str, object]:
    used = 0
    if output_dir.is_dir():
        for path in output_dir.rglob("*"):
            if path.is_file():
                try:
                    used += path.stat().st_size
                except OSError:  # vanished mid-walk
                    continue

    try:
        free = shutil.disk_usage(output_dir if output_dir.is_dir() else output_dir.parent).free
    except OSError:
        free = 0

    return {
        "used_bytes": used,
        "free_bytes": free,
        "soft_limit_bytes": soft_limit_bytes,
        "over_soft_limit": used > soft_limit_bytes,
    }
