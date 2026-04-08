from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.api.auth import router as auth_router
from app.api.recordings import router as recordings_router
from app.api.recordings import watch_router
from app.services.browser_login_service import BrowserLoginService
from app.services.config import PROJECT_ROOT, get_settings
from app.services.cookie_service import CookieService
from app.services.file_service import FileService
from app.services.job_store import JobStore
from app.services.live_status_service import LiveStatusService
from app.services.recorder_service import RecorderService
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
    live_status_service = LiveStatusService(settings)
    recorder_service = RecorderService(settings, job_store, file_service)
    watch_service = WatchService(
        watch_store,
        job_store,
        live_status_service,
        recorder_service,
        settings.watch_poll_interval_seconds,
    )
    app_root = PROJECT_ROOT / "app"
    templates = Jinja2Templates(directory=str(app_root / "templates"))

    app = FastAPI(
        title="TikTok Live Recorder App",
        version="0.1.0",
        description="UI and backend powered by Michele0303/tiktok-live-recorder.",
        root_path=settings.root_path,
    )

    app.state.settings = settings
    app.state.job_store = job_store
    app.state.watch_store = watch_store
    app.state.file_service = file_service
    app.state.cookie_service = cookie_service
    app.state.browser_login_service = browser_login_service
    app.state.live_status_service = live_status_service
    app.state.recorder_service = recorder_service
    app.state.watch_service = watch_service
    app.state.templates = templates

    app.mount("/static", StaticFiles(directory=str(app_root / "static")), name="static")

    app.include_router(auth_router)
    app.include_router(recordings_router)
    app.include_router(watch_router)

    def render_dashboard(request: Request, template_name: str, page_name: str) -> HTMLResponse:
        base_path = settings.root_path.rstrip("/")
        jobs = [job.model_dump(mode="json") for job in job_store.list_jobs()]
        watch_jobs = [job.model_dump(mode="json") for job in watch_store.list_jobs()]
        return templates.TemplateResponse(
            request,
            template_name,
            {
                "request": request,
                "jobs": jobs,
                "watch_jobs": watch_jobs,
                "page_name": page_name,
                "settings": settings,
                "base_path": base_path,
                "cookies_configured": cookie_service.is_configured(),
                "browser_login_status": browser_login_service.status(),
            },
        )

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return render_dashboard(request, "record.html", "record")

    @app.get("/watch", response_class=HTMLResponse)
    def watch_page(request: Request) -> HTMLResponse:
        return render_dashboard(request, "watch.html", "watch")

    @app.get("/favicon.svg")
    def favicon_svg() -> FileResponse:
        return FileResponse(app_root / "static" / "favicon.svg", media_type="image/svg+xml")

    @app.get("/favicon.ico")
    def favicon_ico() -> FileResponse:
        return FileResponse(app_root / "static" / "favicon.svg", media_type="image/svg+xml")

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
