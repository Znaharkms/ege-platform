from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from uuid import uuid4

import yaml
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.api.router import api_router
from app.core.config import get_settings
from app.core.problems import ProblemException, problem_exception_handler
from app.services.admin_notifications import notification_worker


def _load_contract(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as source:
        contract = yaml.safe_load(source)
    contract["info"]["version"] = __version__
    return contract


@asynccontextmanager
async def lifespan(application: FastAPI):
    task = asyncio.create_task(notification_worker())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(
        title="Olesya Blok EGE Platform API",
        lifespan=lifespan,
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Authorization",
            "Content-Type",
            "Idempotency-Key",
            "If-Match",
            "X-Anonymous-Session",
            "X-Request-ID",
        ],
        expose_headers=["ETag", "X-Request-ID"],
    )

    @application.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or str(uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    application.add_exception_handler(ProblemException, problem_exception_handler)
    application.include_router(api_router, prefix=settings.api_prefix)

    media_root = settings.media_root.resolve()
    media_root.mkdir(parents=True, exist_ok=True)
    application.mount("/media", StaticFiles(directory=media_root), name="media")

    admin_web_dir = Path(__file__).resolve().parent / "web" / "admin"
    application.mount(
        "/admin/assets",
        StaticFiles(directory=admin_web_dir / "assets"),
        name="admin-assets",
    )

    @application.get("/admin", include_in_schema=False)
    @application.get("/admin/", include_in_schema=False)
    async def admin_panel() -> FileResponse:
        return FileResponse(admin_web_dir / "index.html")

    trainer_web_dir = Path(__file__).resolve().parent / "web" / "trainer"
    application.mount(
        "/trainer/assets",
        StaticFiles(directory=trainer_web_dir / "assets"),
        name="trainer-assets",
    )

    @application.get("/trainer", include_in_schema=False)
    @application.get("/trainer/", include_in_schema=False)
    @application.get("/trainer/history", include_in_schema=False)
    @application.get("/trainer/history/", include_in_schema=False)
    async def history_trainer() -> FileResponse:
        return FileResponse(trainer_web_dir / "index.html")

    public_web_dir = Path(__file__).resolve().parent / "web" / "public"
    application.mount(
        "/learn/assets", StaticFiles(directory=public_web_dir / "assets"), name="public-assets"
    )

    @application.get("/", include_in_schema=False)
    @application.get("/history", include_in_schema=False)
    @application.get("/history/", include_in_schema=False)
    @application.get("/history/dates", include_in_schema=False)
    @application.get("/history/dates/", include_in_schema=False)
    @application.get("/history/terms", include_in_schema=False)
    @application.get("/history/terms/", include_in_schema=False)
    @application.get("/society", include_in_schema=False)
    @application.get("/society/", include_in_schema=False)
    @application.get("/society/terms", include_in_schema=False)
    @application.get("/society/terms/", include_in_schema=False)
    @application.get("/society/plans", include_in_schema=False)
    @application.get("/society/plans/", include_in_schema=False)
    @application.get("/profile", include_in_schema=False)
    @application.get("/profile/", include_in_schema=False)
    @application.get("/search", include_in_schema=False)
    async def public_platform() -> FileResponse:
        return FileResponse(public_web_dir / "index.html")

    contract_path = settings.openapi_contract_path.resolve()
    application.openapi = lambda: _load_contract(contract_path)
    return application


app = create_app()
