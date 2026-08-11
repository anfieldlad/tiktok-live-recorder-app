"""Scrubbing for subprocess output that gets served back over the API.

Recorder and resolver output carries signed room URLs whose query strings hold
session-derived tokens. Job errors and live-status messages are handed straight
to clients, so they go through here first. The unredacted text stays in the
per-job log files on disk.
"""

from __future__ import annotations

import re


_URL_PATTERN = re.compile(r"https?://[^\s\"'<>]+")
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(session_?ss|sessionid(?:_ss)?|msToken|cookie|authorization)\b\s*[:=]\s*\S+"
)


def redact_sensitive(text: str) -> str:
    """Strip query strings and cookie-looking assignments out of subprocess output."""
    if not text:
        return text

    def strip_query(match: re.Match[str]) -> str:
        base, separator, _ = match.group(0).partition("?")
        return f"{base}?[redacted]" if separator else base

    redacted = _URL_PATTERN.sub(strip_query, text)
    return _SENSITIVE_ASSIGNMENT_PATTERN.sub(lambda match: f"{match.group(1)}=[redacted]", redacted)
