from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.api.auth import router as auth_router
from app.api.recordings import router as recordings_router
from app.services.browser_login_service import BrowserLoginService
from app.services.config import get_settings
from app.services.cookie_service import CookieService
from app.services.file_service import FileService
from app.services.job_store import JobStore
from app.services.live_status_service import LiveStatusService
from app.services.recorder_service import RecorderService


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()
    job_store = JobStore(settings.jobs_file)
    file_service = FileService(settings.output_dir, job_store)
    cookie_service = CookieService(settings.recorder_cookies_file)
    browser_login_service = BrowserLoginService(settings.jobs_file.parents[1], cookie_service)
    live_status_service = LiveStatusService(settings)
    recorder_service = RecorderService(settings, job_store, file_service)
    templates = Jinja2Templates(directory=str(settings.jobs_file.parents[1] / "app" / "templates"))

    app = FastAPI(
        title="TikTok Live Recorder App",
        version="0.1.0",
        description="UI and backend powered by Michele0303/tiktok-live-recorder.",
    )

    app.state.settings = settings
    app.state.job_store = job_store
    app.state.file_service = file_service
    app.state.cookie_service = cookie_service
    app.state.browser_login_service = browser_login_service
    app.state.live_status_service = live_status_service
    app.state.recorder_service = recorder_service
    app.state.templates = templates

    app.include_router(auth_router)
    app.include_router(recordings_router)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        jobs = [job.model_dump(mode="json") for job in job_store.list_jobs()]
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "request": request,
                "jobs": jobs,
                "settings": settings,
                "cookies_configured": cookie_service.is_configured(),
                "browser_login_status": browser_login_service.status(),
            },
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
