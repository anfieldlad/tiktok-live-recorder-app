from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.auth import router as auth_router
from app.api.downloads import router as downloads_router
from app.api.live_relay import router as live_relay_router
from app.api.recordings import router as recordings_router
from app.api.recordings import watch_router
from app.instagram.router import auth_router as instagram_auth_router
from app.instagram.router import downloads_router as instagram_downloads_router
from app.instagram.services.instagram_browser_login_service import InstagramBrowserLoginService
from app.instagram.services.instagram_cookie_service import InstagramCookieService
from app.instagram.services.instagram_download_service import InstagramDownloadService
from app.services.browser_login_service import BrowserLoginService
from app.services.cleanup_service import CleanupService
from app.services.config import PROJECT_ROOT, get_settings
from app.services.cookie_service import CookieService
from app.services.download_store import DownloadStore
from app.services.file_service import FileService
from app.services.job_store import JobStore
from app.services.live_status_service import LiveStatusService
from app.services.post_download_service import PostDownloadService
from app.services.recorder_service import RecorderService
from app.services.retention import RetentionPolicy
from app.services.storage_report import storage_report
from app.services.watch_service import WatchService
from app.services.watch_store import WatchStore


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    job_store = JobStore(settings.jobs_file)
    watch_store = WatchStore(settings.watch_jobs_file)
    file_service = FileService(settings.output_dir, job_store)
    cookie_service = CookieService(settings.recorder_cookies_file)
    browser_login_service = BrowserLoginService(settings.jobs_file.parents[1], cookie_service)
    live_status_service = LiveStatusService(settings, cookie_service)
    recorder_service = RecorderService(settings, job_store, file_service)
    download_store = DownloadStore(settings.downloads_file)
    post_download_service = PostDownloadService(settings.output_dir, cookie_service, download_store)
    instagram_cookie_service = InstagramCookieService(settings.instagram_cookies_file)
    instagram_browser_login_service = InstagramBrowserLoginService(settings.jobs_file.parents[1], instagram_cookie_service)
    instagram_download_service = InstagramDownloadService(
        settings.output_dir, instagram_cookie_service, download_store
    )
    retention_policy = RetentionPolicy.from_settings(settings)
    cleanup_service = CleanupService(settings, job_store, download_store, retention_policy)
    watch_service = WatchService(
        watch_store,
        job_store,
        live_status_service,
        recorder_service,
        settings.watch_poll_interval_seconds,
    )
    app_root = PROJECT_ROOT / "app"
    templates = Jinja2Templates(directory=str(app_root / "templates"))

    # The interactive docs publish the whole API surface, so they stay off in
    # production until the API is behind authentication.
    app = FastAPI(
        title="TikTok Media Saver",
        version="0.1.0",
        description="Local tools for saving TikTok Live recordings and public TikTok posts.",
        root_path=settings.root_path,
        docs_url=None if settings.is_production else "/docs",
        redoc_url=None if settings.is_production else "/redoc",
        openapi_url=None if settings.is_production else "/openapi.json",
    )

    app.state.settings = settings
    app.state.job_store = job_store
    app.state.watch_store = watch_store
    app.state.file_service = file_service
    app.state.cookie_service = cookie_service
    app.state.browser_login_service = browser_login_service
    app.state.live_status_service = live_status_service
    app.state.recorder_service = recorder_service
    app.state.post_download_service = post_download_service
    app.state.instagram_cookie_service = instagram_cookie_service
    app.state.instagram_browser_login_service = instagram_browser_login_service
    app.state.instagram_download_service = instagram_download_service
    app.state.watch_service = watch_service
    app.state.download_store = download_store
    app.state.cleanup_service = cleanup_service
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(app_root / "static")), name="static")

    app.include_router(auth_router)
    app.include_router(downloads_router)
    app.include_router(live_relay_router)
    app.include_router(recordings_router)
    app.include_router(watch_router)
    app.include_router(instagram_downloads_router)
    app.include_router(instagram_auth_router)

    def render_dashboard(request: Request, template_name: str, page_name: str, platform: str = "tiktok") -> HTMLResponse:
        base_path = settings.root_path.rstrip("/")
        jobs = [job.model_dump(mode="json") for job in job_store.list_jobs()]
        watch_jobs = [job.model_dump(mode="json") for job in watch_store.list_jobs()]
        if platform == "instagram":
            cookies_configured = instagram_cookie_service.is_configured()
            browser_login_status = instagram_browser_login_service.status()
        else:
            cookies_configured = cookie_service.is_configured()
            browser_login_status = browser_login_service.status()
        return templates.TemplateResponse(
            request,
            template_name,
            {
                "request": request,
                "jobs": jobs,
                "watch_jobs": watch_jobs,
                "page_name": page_name,
                "platform": platform,
                "settings": settings,
                "base_path": base_path,
                "cookies_configured": cookies_configured,
                "browser_login_status": browser_login_status,
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return render_dashboard(request, "record.html", "record")

    @app.get("/watch", response_class=HTMLResponse)
    def watch_page(request: Request) -> HTMLResponse:
        return render_dashboard(request, "watch.html", "watch")

    @app.get("/download", response_class=HTMLResponse)
    def download_page(request: Request) -> HTMLResponse:
        return render_dashboard(request, "download.html", "download")

    @app.get("/instagram", response_class=HTMLResponse)
    def instagram_page(request: Request) -> HTMLResponse:
        return render_dashboard(request, "instagram_download.html", "instagram", platform="instagram")

    @app.api_route("/favicon.svg", methods=["GET", "HEAD"])
    def favicon_svg() -> FileResponse:
        return FileResponse(app_root / "static" / "favicon.svg", media_type="image/svg+xml")

    @app.api_route("/favicon.ico", methods=["GET", "HEAD"])
    def favicon_ico() -> FileResponse:
        return FileResponse(app_root / "static" / "favicon.svg", media_type="image/svg+xml")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    def redact_details(details: dict[str, object]) -> dict[str, object]:
        """Keep the health payload useful without handing an unauthenticated
        caller our filesystem layout and live process ids."""
        if not settings.is_production:
            return details
        services = dict(details["services"])  # type: ignore[arg-type]
        recorder = dict(services["recorder"])  # type: ignore[arg-type]
        recorder.pop("active_processes", None)
        services["recorder"] = recorder
        details["services"] = services
        details["stores"] = {
            name: {key: value for key, value in diagnostics.items() if not key.endswith("file")}
            for name, diagnostics in details["stores"].items()  # type: ignore[union-attr]
        }
        return details

    @app.get("/health/details")
    def health_details() -> dict[str, object]:
        jobs = job_store.list_jobs()
        watch_jobs = watch_store.list_jobs()
        active_recording = job_store.get_active_job()
        return redact_details({
            "status": "ok",
            "app_env": settings.app_env,
            "root_path": settings.root_path,
            "cookies_configured": cookie_service.is_configured(),
            "browser_login": browser_login_service.status(),
            "instagram": {
                "cookies_configured": instagram_cookie_service.is_configured(),
                "browser_login": instagram_browser_login_service.status(),
            },
            "recordings": {
                "total": len(jobs),
                "active_job_id": active_recording.id if active_recording else None,
                "active_count": sum(1 for job in jobs if job.status in {"queued", "running"}),
            },
            "watches": {
                "total": len(watch_jobs),
                "active_count": sum(1 for job in watch_jobs if job.status in {"watching", "recording"}),
            },
            "services": {
                "recorder": recorder_service.diagnostics(),
                "watch": watch_service.diagnostics(),
                "cleanup": cleanup_service.diagnostics(),
            },
            "stores": {
                "jobs": job_store.diagnostics(),
                "watch_jobs": watch_store.diagnostics(),
                "downloads": download_store.diagnostics(),
            },
            "storage": storage_report(settings.output_dir, retention_policy.storage_soft_limit_bytes),
        })

    return app


app = create_app()
