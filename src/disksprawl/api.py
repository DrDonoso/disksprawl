"""The HTTP API under /api. Every route but version and sign-in needs a session when a password
is configured; every write needs the same-origin checks of the security middleware."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import cron, paths, queries, settings_store
from .auth import COOKIE, Auth, client_address, get_auth, require_session
from .config import Settings
from .db import Directory, Scan, transaction, utcnow
from .engines import EngineInfo
from .errors import AppError
from .scanning import Scanner
from .scheduler import Scheduler
from .schemas import (
    AuthStatusOut,
    BrowseEntryOut,
    BrowseOut,
    CapacityHistoryOut,
    CronPreviewOut,
    DiffOut,
    DirectoryIn,
    DirectoryListOut,
    DirectorySummaryOut,
    EngineListOut,
    EngineOut,
    LoginIn,
    OrderIn,
    ProgressOut,
    QueueOut,
    ScanListOut,
    ScanOut,
    SettingsIn,
    SettingsOut,
    TreeOut,
    VersionOut,
    WhatsNewOut,
    WhatsNewSeenIn,
)

public = APIRouter(prefix="/api")
router = APIRouter(prefix="/api", dependencies=[Depends(require_session)])


def config(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def scanner(request: Request) -> Scanner:
    value: Scanner = request.app.state.scanner
    return value


def scheduler(request: Request) -> Scheduler:
    value: Scheduler = request.app.state.scheduler
    return value


def engines(request: Request) -> dict[str, EngineInfo]:
    found: dict[str, EngineInfo] = request.app.state.engines
    return found


def database(request: Request):  # noqa: ANN201 - a session generator for Depends
    with request.app.state.sessions() as session:
        yield session


DB = Annotated[Session, Depends(database)]


def next_runs(request: Request, db: Session) -> dict[int, datetime | None]:
    jobs = scheduler(request)
    return {identifier: jobs.next_run(identifier) for identifier in db.scalars(select(Directory.id))}


def summary(request: Request, db: Session, directory_id: int) -> DirectorySummaryOut:
    found = queries.summaries(db, scanner(request), next_runs(request, db), directory_id)
    if not found:
        raise AppError("directory_not_found", 404)
    return found[0]


# Public


@public.get("/version", response_model=VersionOut)
def version(request: Request) -> VersionOut:
    settings = config(request)
    return VersionOut(version=settings.image_tag, build_date=settings.build_date)


@public.get("/auth/status", response_model=AuthStatusOut)
def auth_status(request: Request) -> AuthStatusOut:
    auth = get_auth(request)
    authenticated = not auth.enabled or auth.valid(request.cookies.get(COOKIE))
    return AuthStatusOut(enabled=auth.enabled, authenticated=authenticated)


@public.post("/auth/login", status_code=204, response_class=Response)
def login(body: LoginIn, request: Request, response: Response) -> None:
    auth: Auth = get_auth(request)
    if not auth.enabled:
        raise AppError("auth_disabled", 409)
    client = client_address(request)
    retry = auth.limiter.retry_after(client)
    if retry is not None:
        raise AppError("too_many_attempts", 429, headers={"Retry-After": str(retry)})
    if not auth.password_matches(body.password):
        auth.limiter.failed(client)
        raise AppError("invalid_password", 401)
    auth.limiter.succeeded(client)
    value, expires_at = auth.create()
    response.set_cookie(
        COOKIE,
        value,
        max_age=int((expires_at - utcnow()).total_seconds()),
        httponly=True,
        samesite="strict",
        secure=auth.secure,
        path="/",
    )


@public.post("/auth/logout", status_code=204, response_class=Response)
def logout(request: Request, response: Response) -> None:
    get_auth(request).revoke(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="strict")


# Engines, folders and schedules


@router.get("/engines", response_model=EngineListOut)
def list_engines(request: Request) -> EngineListOut:
    return EngineListOut(
        items=[
            EngineOut(
                id=info.id,
                available=info.available,
                version=info.version,
                max_threads=info.max_threads,
                progress=info.progress,
            )
            for info in engines(request).values()
        ]
    )


@router.get("/browse", response_model=BrowseOut)
def browse(request: Request, path: Annotated[str | None, Query(max_length=4096)] = None) -> BrowseOut:
    settings = config(request)
    folder, parent, entries = paths.browse(path, settings.scan_root)
    return BrowseOut(
        root=paths.normalize(str(settings.scan_root)),
        path=folder,
        parent=parent,
        entries=[BrowseEntryOut(name=name, path=entry) for name, entry in entries],
    )


@router.get("/cron/preview", response_model=CronPreviewOut)
def cron_preview(
    request: Request, expression: Annotated[str, Query(max_length=cron.MAX_LENGTH)]
) -> CronPreviewOut:
    settings = config(request)
    normalized = cron.normalize(expression, settings.zone)
    if normalized is None:
        raise AppError("invalid_cron", fields={"cron": "invalid_cron"})
    return CronPreviewOut(
        expression=normalized,
        timezone=settings.tz,
        next_runs=cron.next_runs(normalized, settings.zone),
    )


# Directories


def validated(request: Request, db: Session, body: DirectoryIn, current: int | None) -> DirectoryIn:
    settings = config(request)
    path = paths.normalize(body.path)
    paths.resolve(path, settings.scan_root)
    duplicate = db.scalar(select(Directory.id).where(Directory.path == path))
    if duplicate is not None and duplicate != current:
        raise AppError("duplicate_path", 409, fields={"path": "duplicate_path"})
    engine = engines(request).get(body.engine)
    if engine is None or not engine.available:
        raise AppError("engine_unavailable", fields={"engine": "engine_unavailable"})
    threads = 1 if engine.max_threads == 1 else body.threads
    if threads > engine.max_threads:
        raise AppError("threads_out_of_range", fields={"threads": "threads_out_of_range"})
    return body.model_copy(
        update={"path": path, "threads": threads, "cron": cron.normalize(body.cron, settings.zone)}
    )


@router.get("/directories", response_model=DirectoryListOut)
def list_directories(request: Request, db: DB) -> DirectoryListOut:
    return DirectoryListOut(items=queries.summaries(db, scanner(request), next_runs(request, db)))


@router.post("/directories", response_model=DirectorySummaryOut, status_code=201)
def create_directory(body: DirectoryIn, request: Request) -> DirectorySummaryOut:
    with transaction(request.app.state.sessions) as db:
        clean = validated(request, db, body, None)
        position = (db.scalar(select(func.max(Directory.position))) or 0) + 1
        directory = Directory(**clean.model_dump(), position=position)
        db.add(directory)
        db.flush()
        scheduler(request).sync(directory)
        directory_id = directory.id
    scheduler(request).measure_directory(directory_id)
    with request.app.state.sessions() as db:
        return summary(request, db, directory_id)


@router.put("/directories/order", response_model=DirectoryListOut)
def order_directories(body: OrderIn, request: Request) -> DirectoryListOut:
    with transaction(request.app.state.sessions) as db:
        directories = {directory.id: directory for directory in db.scalars(select(Directory))}
        if sorted(body.ids) != sorted(directories):
            raise AppError("invalid_order", fields={"ids": "invalid_order"})
        for position, identifier in enumerate(body.ids, start=1):
            directories[identifier].position = position
    with request.app.state.sessions() as db:
        return DirectoryListOut(
            items=queries.summaries(db, scanner(request), next_runs(request, db))
        )


@router.get("/directories/{directory_id}/overview", response_model=DirectorySummaryOut)
def overview(directory_id: int, request: Request, db: DB) -> DirectorySummaryOut:
    return summary(request, db, directory_id)


@router.put("/directories/{directory_id}", response_model=DirectorySummaryOut)
def update_directory(directory_id: int, body: DirectoryIn, request: Request) -> DirectorySummaryOut:
    with transaction(request.app.state.sessions) as db:
        directory = queries.get_directory(db, directory_id)
        clean = validated(request, db, body, directory_id)
        for key, value in clean.model_dump().items():
            setattr(directory, key, value)
        directory.updated_at = utcnow()
        db.flush()
        scheduler(request).sync(directory)
    with request.app.state.sessions() as db:
        return summary(request, db, directory_id)


@router.delete("/directories/{directory_id}", status_code=204, response_class=Response)
def delete_directory(directory_id: int, request: Request) -> None:
    with request.app.state.sessions() as db:
        queries.get_directory(db, directory_id)
    scheduler(request).remove(directory_id)
    # Only DiskSprawl's own records go, in batches: the folder and its files are never touched.
    scanner(request).forget(directory_id)


@router.post("/directories/{directory_id}/scan", response_model=ProgressOut, status_code=202)
def scan_now(directory_id: int, request: Request) -> ProgressOut:
    queue = scanner(request)
    scan_id = queue.enqueue(directory_id, "manual")
    with request.app.state.sessions() as db:
        scan = db.get(Scan, scan_id)
        if scan is None:
            raise AppError("scan_not_found", 404)
        return queries.progress_out(scan, queue.live(), queue.queue_positions(db))


@router.get("/directories/{directory_id}/tree", response_model=TreeOut)
def tree(
    directory_id: int,
    db: DB,
    scan: int | None = None,
    path: Annotated[str, Query(max_length=65536)] = "",
) -> TreeOut:
    return queries.tree(db, directory_id, scan, path)


@router.get("/directories/{directory_id}/diff", response_model=DiffOut)
def diff(
    directory_id: int,
    db: DB,
    from_scan: Annotated[int | None, Query(alias="from")] = None,
    to_scan: Annotated[int | None, Query(alias="to")] = None,
    path: Annotated[str, Query(max_length=65536)] = "",
) -> DiffOut:
    return queries.diff(db, directory_id, from_scan, to_scan, path)


@router.get("/directories/{directory_id}/scans", response_model=ScanListOut)
def directory_scans(
    directory_id: int,
    db: DB,
    days: Annotated[int | None, Query(ge=1, le=3660)] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> ScanListOut:
    return ScanListOut(items=queries.scans(db, directory_id, days, limit))


@router.get("/directories/{directory_id}/capacity", response_model=CapacityHistoryOut)
def capacity(
    directory_id: int, db: DB, days: Annotated[int, Query(ge=1, le=3660)] = 30
) -> CapacityHistoryOut:
    return queries.capacity_history(db, directory_id, days)


# Scans and the queue


@router.get("/scans/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: int, db: DB) -> ScanOut:
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise AppError("scan_not_found", 404)
    return ScanOut.model_validate(scan)


@router.post("/scans/{scan_id}/cancel", response_model=ScanOut)
def cancel_scan(scan_id: int, request: Request) -> ScanOut:
    scanner(request).cancel(scan_id)
    with request.app.state.sessions() as db:
        scan = db.get(Scan, scan_id)
        if scan is None:
            raise AppError("scan_not_found", 404)
        return ScanOut.model_validate(scan)


@router.get("/queue", response_model=QueueOut)
def queue(request: Request, db: DB) -> QueueOut:
    return QueueOut(items=queries.active_progress(db, scanner(request)))


# Settings


def settings_out(request: Request, values: dict[str, int]) -> SettingsOut:
    return SettingsOut(**values, timezone=config(request).tz)


@router.get("/settings", response_model=SettingsOut)
def get_settings(request: Request, db: DB) -> SettingsOut:
    return settings_out(request, settings_store.load(db))


@router.get("/whats-new", response_model=WhatsNewOut)
def get_whats_new(db: DB) -> WhatsNewOut:
    return WhatsNewOut(seen=settings_store.load_seen_notes(db))


@router.post("/whats-new/seen", response_model=WhatsNewOut)
def post_whats_new_seen(body: WhatsNewSeenIn, request: Request) -> WhatsNewOut:
    with transaction(request.app.state.sessions) as db:
        return WhatsNewOut(seen=settings_store.mark_notes_seen(db, body.ids))


@router.put("/settings", response_model=SettingsOut)
def put_settings(body: SettingsIn, request: Request) -> SettingsOut:
    if body.critical_percent <= body.warn_percent:
        raise AppError("thresholds_out_of_order", fields={"critical_percent": "thresholds_out_of_order"})
    with transaction(request.app.state.sessions) as db:
        before = settings_store.load(db)
        values = settings_store.save(db, body.model_dump())
        directory_ids = db.scalars(select(Directory.id)).all()
    if values["keep_scans"] < before["keep_scans"]:
        # In batches, outside the settings' transaction: a large history takes a while to delete.
        for identifier in directory_ids:
            scanner(request).prune(identifier, values["keep_scans"])
    if values["capacity_interval_minutes"] != before["capacity_interval_minutes"]:
        scheduler(request).schedule_capacity(values["capacity_interval_minutes"])
    return settings_out(request, values)
