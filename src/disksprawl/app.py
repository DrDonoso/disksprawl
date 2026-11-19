"""The application factory: API, scan queue, scheduler, notifications and the built web app, in one
process."""

import logging
import traceback
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.types import ASGIApp

from . import api
from .auth import Auth
from .config import Settings
from .db import create_db_engine, migrate, session_factory
from .engines import detect_engines
from .errors import AppError
from .notifications import Notifier
from .scanning import Scanner
from .scheduler import Scheduler
from .schemas import HealthOut
from .security import SecurityMiddleware

log = logging.getLogger("disksprawl")
# Client-side routes of the web app: everything else outside /api is a static file or a 404.
APP_ROUTES = ("", "settings")
STATIC_FILES = (
    "icon.svg",
    "favicon.ico",
    "apple-touch-icon.png",
    "icon-192.png",
    "icon-512.png",
    "icon-maskable-512.png",
    "manifest.webmanifest",
    "robots.txt",
    # The service worker that shows the notifications, and their icon for Android's status bar.
    "sw.js",
    "badge-96.png",
)


class DiskSprawlApp(FastAPI):
    def build_middleware_stack(self) -> ASGIApp:
        return SecurityMiddleware(super().build_middleware_stack(), lambda: self.state.settings)


def validation_fields(errors: list[dict[str, object]]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for error in errors:
        raw_location = error.get("loc", ())
        parts = raw_location if isinstance(raw_location, (tuple, list)) else ()
        location = [str(part) for part in parts if part not in ("body", "query")]
        if not location:
            continue
        message = str(error.get("msg", ""))
        code = message.removeprefix("Value error, ") if message.startswith("Value error, ") else ""
        fields.setdefault(location[0], code or "invalid")
    return fields


def create_app(config: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        settings = config or Settings()
        application.state.settings = settings
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        migrate(settings.database_path)
        engine = create_db_engine(settings.database_path)
        sessions = session_factory(engine)
        application.state.sessions = sessions
        found = detect_engines()
        application.state.engines = found
        for info in found.values():
            log.info(
                "Engine %s: %s",
                info.id,
                f"{info.version} ({info.binary or 'built in'})" if info.available else "missing",
            )
        application.state.auth = Auth(settings, sessions)
        notifier = Notifier(sessions, settings)
        application.state.notifier = notifier
        scanner = Scanner(
            sessions, settings, lambda: application.state.engines, on_finished=notifier.wake
        )
        application.state.scanner = scanner
        scheduler = Scheduler(sessions, settings, scanner)
        application.state.scheduler = scheduler
        scanner.start()
        scheduler.start()
        notifier.start()
        try:
            yield
        finally:
            scheduler.shutdown()
            scanner.stop()
            notifier.stop()
            engine.dispose()

    application = DiskSprawlApp(
        title="DiskSprawl",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @application.exception_handler(AppError)
    async def known_error(request: Request, error: AppError) -> JSONResponse:
        content: dict[str, object] = {"code": error.code}
        if error.fields:
            content["fields"] = error.fields
        return JSONResponse(content, status_code=error.status, headers=error.headers)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        content: dict[str, object] = {"code": "invalid_request"}
        fields = validation_fields(list(error.errors()))
        if fields:
            content["fields"] = fields
        return JSONResponse(content, status_code=422)

    @application.exception_handler(Exception)
    async def unexpected(request: Request, error: Exception) -> JSONResponse:
        # Messages can quote folder names: log the type and the stack, not the text.
        log.error(
            "Unhandled %s\n%s",
            type(error).__name__,
            "".join(traceback.format_tb(error.__traceback__)),
        )
        return JSONResponse({"code": "internal_error"}, status_code=500)

    application.include_router(api.public)
    application.include_router(api.router)

    @application.get("/health", response_model=HealthOut)
    def health(request: Request) -> HealthOut:
        with request.app.state.sessions() as db:
            db.connection().exec_driver_sql("SELECT 1")
        return HealthOut(status="ok")

    @application.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
                           include_in_schema=False)
    def unknown_api(path: str) -> Response:
        raise AppError("not_found", 404)

    def frontend_dir(request: Request) -> Path:
        directory: Path = request.app.state.settings.frontend_dir
        return directory

    @application.get("/assets/{path:path}", include_in_schema=False)
    def assets(path: str, request: Request) -> Response:
        root = (frontend_dir(request) / "assets").resolve()
        target = (root / path).resolve()
        if root not in target.parents or not target.is_file():
            raise AppError("not_found", 404)
        return FileResponse(target)

    @application.get("/{path:path}", include_in_schema=False)
    def web_app(path: str, request: Request) -> Response:
        dist = frontend_dir(request)
        if path in STATIC_FILES and (dist / path).is_file():
            return FileResponse(dist / path)
        first = path.split("/", 1)[0]
        is_route = path.rstrip("/") in APP_ROUTES or (
            first == "directories" and path.count("/") == 1 and path.split("/")[1].isdigit()
        )
        if not is_route:
            raise AppError("not_found", 404)
        if not (dist / "index.html").is_file():
            raise AppError("frontend_not_built", 503)
        return FileResponse(dist / "index.html")

    return application


def app() -> FastAPI:
    """Uvicorn factory: `uvicorn --factory disksprawl.app:app`."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Every schedule change and capacity measurement would be logged otherwise.
    for name in ("apscheduler", "alembic.runtime.plugins"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return create_app()
