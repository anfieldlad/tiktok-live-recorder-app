from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi import HTTPException, status

from app.api.security import require_key, session_allowed

from app.models.recording import (
    TikTokBrowserLoginStatusResponse,
    TikTokCookieRequest,
    TikTokCookieStatusResponse,
)


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status", response_model=TikTokCookieStatusResponse)
def get_auth_status(request: Request) -> TikTokCookieStatusResponse:
    """Open on purpose, so the Sessions drawer renders for anyone."""
    cookie_service = request.app.state.cookie_service
    settings = request.app.state.settings
    allowed = session_allowed(request)
    return TikTokCookieStatusResponse(
        configured=cookie_service.is_configured(),
        cookie_file=str(settings.recorder_cookies_file.resolve()) if allowed else None,
        session_allowed=allowed,
    )


@router.post(
    "/tiktok-cookies",
    response_model=TikTokCookieStatusResponse,
    dependencies=[Depends(require_key)],
)
def save_tiktok_cookies(request: Request, payload: TikTokCookieRequest) -> TikTokCookieStatusResponse:
    cookie_service = request.app.state.cookie_service
    settings = request.app.state.settings
    if payload.cookies:
        cookie_service.save_cookie_map(payload.cookies)
    else:
        cookie_service.save_session_cookie(payload.session_ss)
    return TikTokCookieStatusResponse(
        configured=True,
        cookie_file=str(settings.recorder_cookies_file.resolve()),
    )


@router.post(
    "/import-browser/{browser_name}",
    response_model=TikTokCookieStatusResponse,
    dependencies=[Depends(require_key)],
)
def import_tiktok_cookies_from_browser(request: Request, browser_name: str) -> TikTokCookieStatusResponse:
    cookie_service = request.app.state.cookie_service
    settings = request.app.state.settings
    try:
        cookie_service.import_from_browser(browser_name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to import TikTok cookies from {browser_name}: {exc}",
        ) from exc
    return TikTokCookieStatusResponse(
        configured=True,
        cookie_file=str(settings.recorder_cookies_file.resolve()),
    )


@router.delete(
    "/tiktok-cookies",
    response_model=TikTokCookieStatusResponse,
    dependencies=[Depends(require_key)],
)
def clear_tiktok_cookies(request: Request) -> TikTokCookieStatusResponse:
    cookie_service = request.app.state.cookie_service
    settings = request.app.state.settings
    cookie_service.clear()
    return TikTokCookieStatusResponse(
        configured=False,
        cookie_file=str(settings.recorder_cookies_file.resolve()),
    )


@router.get("/login-browser/status", response_model=TikTokBrowserLoginStatusResponse)
def get_browser_login_status(request: Request) -> TikTokBrowserLoginStatusResponse:
    browser_login_service = request.app.state.browser_login_service
    return TikTokBrowserLoginStatusResponse(**browser_login_service.status())


@router.post(
    "/login-browser/{browser_name}/start",
    response_model=TikTokBrowserLoginStatusResponse,
    dependencies=[Depends(require_key)],
)
def start_browser_login(request: Request, browser_name: str) -> TikTokBrowserLoginStatusResponse:
    browser_login_service = request.app.state.browser_login_service
    try:
        result = browser_login_service.start_login(browser_name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to open TikTok login in {browser_name}: {exc}",
        ) from exc
    return TikTokBrowserLoginStatusResponse(**result)


@router.post(
    "/login-browser/capture",
    response_model=TikTokBrowserLoginStatusResponse,
    dependencies=[Depends(require_key)],
)
def capture_browser_login(request: Request) -> TikTokBrowserLoginStatusResponse:
    browser_login_service = request.app.state.browser_login_service
    try:
        result = browser_login_service.capture_session()
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"failed to capture TikTok session: {exc}",
        ) from exc
    return TikTokBrowserLoginStatusResponse(**result)


@router.post(
    "/login-browser/close",
    response_model=TikTokBrowserLoginStatusResponse,
    dependencies=[Depends(require_key)],
)
def close_browser_login(request: Request) -> TikTokBrowserLoginStatusResponse:
    browser_login_service = request.app.state.browser_login_service
    return TikTokBrowserLoginStatusResponse(**browser_login_service.close())
