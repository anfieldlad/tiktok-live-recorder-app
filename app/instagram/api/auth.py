from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator

from app.api.security import require_key, session_allowed


router = APIRouter(prefix="/instagram/auth", tags=["instagram-auth"])


class InstagramCookieRequest(BaseModel):
    sessionid: str = Field(min_length=1)

    @field_validator("sessionid")
    @classmethod
    def normalize_sessionid(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("sessionid must not be empty")
        return normalized


class InstagramCookieStatusResponse(BaseModel):
    configured: bool
    # See TikTokCookieStatusResponse: a path is not an anonymous caller's
    # business, and `session_allowed` is the field the UI needs.
    cookie_file: str | None = None
    session_allowed: bool = True


class InstagramBrowserLoginStatusResponse(BaseModel):
    browser_open: bool
    browser_name: str | None = None
    authenticated: bool
    cookies_configured: bool
    browser_launch_supported: bool


@router.get("/status", response_model=InstagramCookieStatusResponse)
def get_auth_status(request: Request) -> InstagramCookieStatusResponse:
    """Open on purpose, so the Sessions drawer renders for anyone."""
    cookie_service = request.app.state.instagram_cookie_service
    settings = request.app.state.settings
    allowed = session_allowed(request)
    return InstagramCookieStatusResponse(
        configured=cookie_service.is_configured(),
        cookie_file=str(settings.instagram_cookies_file.resolve()) if allowed else None,
        session_allowed=allowed,
    )


@router.post(
    "/cookies",
    response_model=InstagramCookieStatusResponse,
    dependencies=[Depends(require_key)],
)
def save_instagram_cookies(request: Request, payload: InstagramCookieRequest) -> InstagramCookieStatusResponse:
    cookie_service = request.app.state.instagram_cookie_service
    settings = request.app.state.settings
    cookie_service.save_session_cookie(payload.sessionid)
    return InstagramCookieStatusResponse(
        configured=True,
        cookie_file=str(settings.instagram_cookies_file.resolve()),
    )


@router.post(
    "/import-browser/{browser_name}",
    response_model=InstagramCookieStatusResponse,
    dependencies=[Depends(require_key)],
)
def import_instagram_cookies_from_browser(request: Request, browser_name: str) -> InstagramCookieStatusResponse:
    cookie_service = request.app.state.instagram_cookie_service
    settings = request.app.state.settings
    try:
        cookie_service.import_from_browser(browser_name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to import Instagram cookies from {browser_name}: {exc}",
        ) from exc
    return InstagramCookieStatusResponse(
        configured=True,
        cookie_file=str(settings.instagram_cookies_file.resolve()),
    )


@router.delete(
    "/cookies",
    response_model=InstagramCookieStatusResponse,
    dependencies=[Depends(require_key)],
)
def clear_instagram_cookies(request: Request) -> InstagramCookieStatusResponse:
    cookie_service = request.app.state.instagram_cookie_service
    settings = request.app.state.settings
    cookie_service.clear()
    return InstagramCookieStatusResponse(
        configured=False,
        cookie_file=str(settings.instagram_cookies_file.resolve()),
    )


@router.get("/login-browser/status", response_model=InstagramBrowserLoginStatusResponse)
def get_browser_login_status(request: Request) -> InstagramBrowserLoginStatusResponse:
    browser_login_service = request.app.state.instagram_browser_login_service
    return InstagramBrowserLoginStatusResponse(**browser_login_service.status())


@router.post(
    "/login-browser/{browser_name}/start",
    response_model=InstagramBrowserLoginStatusResponse,
    dependencies=[Depends(require_key)],
)
def start_browser_login(request: Request, browser_name: str) -> InstagramBrowserLoginStatusResponse:
    browser_login_service = request.app.state.instagram_browser_login_service
    try:
        result = browser_login_service.start_login(browser_name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to open Instagram login in {browser_name}: {exc}",
        ) from exc
    return InstagramBrowserLoginStatusResponse(**result)


@router.post(
    "/login-browser/capture",
    response_model=InstagramBrowserLoginStatusResponse,
    dependencies=[Depends(require_key)],
)
def capture_browser_login(request: Request) -> InstagramBrowserLoginStatusResponse:
    browser_login_service = request.app.state.instagram_browser_login_service
    try:
        result = browser_login_service.capture_session()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to capture Instagram session: {exc}",
        ) from exc
    return InstagramBrowserLoginStatusResponse(**result)


@router.post(
    "/login-browser/close",
    response_model=InstagramBrowserLoginStatusResponse,
    dependencies=[Depends(require_key)],
)
def close_browser_login(request: Request) -> InstagramBrowserLoginStatusResponse:
    browser_login_service = request.app.state.instagram_browser_login_service
    return InstagramBrowserLoginStatusResponse(**browser_login_service.close())
