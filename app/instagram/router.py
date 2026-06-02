from __future__ import annotations

from app.instagram.api.auth import router as auth_router
from app.instagram.api.downloads import router as downloads_router

__all__ = ["auth_router", "downloads_router"]
