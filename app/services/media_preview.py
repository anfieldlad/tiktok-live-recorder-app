"""Which saved files a browser may show inline as a thumbnail.

The download routes always send `application/octet-stream` as an attachment.
A preview is served inline, so it is limited to plain image and video types: a
file the engines wrote with any other extension (an .html or .svg would run on
this origin) gets no preview at all.
"""

from __future__ import annotations

from pathlib import Path

PREVIEW_MEDIA_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
}

PREVIEW_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    # The register re-renders on every poll; let the browser keep the thumbnail.
    "Cache-Control": "private, max-age=3600",
}


def preview_media_type(path: Path) -> str | None:
    return PREVIEW_MEDIA_TYPES.get(path.suffix.lower())
