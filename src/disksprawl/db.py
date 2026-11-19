"""SQLite storage: the models, the engine with its pragmas, and the migration runner."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    event,
    false,
    text,
)
from sqlalchemy.engine import Dialect, Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Aware UTC datetimes in Python, naive UTC text in SQLite (which has no time zones)."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Naive datetimes are not stored: use an aware UTC datetime")
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        return None if value is None else value.replace(tzinfo=UTC)


class Base(DeclarativeBase):
    type_annotation_map = {datetime: UTCDateTime, int: BigInteger}


class Directory(Base):
    __tablename__ = "directories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    path: Mapped[str] = mapped_column(String(4096), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    cron: Mapped[str | None] = mapped_column(String(120))
    engine: Mapped[str] = mapped_column(String(10))
    threads: Mapped[int] = mapped_column(Integer)
    pace: Mapped[str] = mapped_column(String(10))
    low_priority: Mapped[bool] = mapped_column(Boolean)
    measure: Mapped[str] = mapped_column(String(10))
    one_filesystem: Mapped[bool] = mapped_column(Boolean)
    excludes: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Scan(Base):
    __tablename__ = "scans"
    __table_args__ = (
        Index("ix_scans_directory_status", "directory_id", "status", "finished_at"),
        Index("ix_scans_status_queued", "status", "queued_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    directory_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("directories.id", ondelete="CASCADE")
    )
    # queued, running, completed, failed, cancelled or interrupted.
    status: Mapped[str] = mapped_column(String(12))
    # scheduled or manual.
    trigger: Mapped[str] = mapped_column(String(10))
    engine: Mapped[str] = mapped_column(String(10))
    # The directory's scan options when the scan was queued.
    options: Mapped[dict[str, Any]] = mapped_column(JSON)
    queued_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    total_bytes: Mapped[int | None]
    file_count: Mapped[int | None]
    dir_count: Mapped[int | None]
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    error_sample: Mapped[list[dict[str, str]]] = mapped_column(JSON, default=list)
    # Why a scan failed: an error code, sometimes followed by details.
    message: Mapped[str | None] = mapped_column(String(1000))
    capacity_total: Mapped[int | None]
    capacity_used: Mapped[int | None]
    capacity_free: Mapped[int | None]
    # Whether scan_entries still holds this scan's folders (retention keeps the latest ones).
    has_detail: Mapped[bool] = mapped_column(Boolean, default=False)
    # Whether the notifications of this scan's end were queued (none for scans still queued or
    # running). The scans of versions without notifications count as notified.
    notified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())


class PathNode(Base):
    """A folder of a directory, by parent and name: the same id in every scan, so scans join."""

    __tablename__ = "paths"
    __table_args__ = (
        UniqueConstraint("directory_id", "parent_id", "name", name="uq_paths_parent_name"),
        Index("ix_paths_parent", "parent_id"),
        # SQLite treats NULLs as distinct in unique constraints: one root per directory.
        Index(
            "uq_paths_root", "directory_id", unique=True, sqlite_where=text("parent_id IS NULL")
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    directory_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("directories.id", ondelete="CASCADE")
    )
    parent_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("paths.id", ondelete="CASCADE")
    )
    name: Mapped[str] = mapped_column(String(4096))


class ScanEntry(Base):
    """One folder of a scan at least as large as the minimum stored size (the root always)."""

    __tablename__ = "scan_entries"
    __table_args__ = (Index("ix_scan_entries_path", "path_id"),)

    scan_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("scans.id", ondelete="CASCADE"), primary_key=True
    )
    path_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("paths.id", ondelete="CASCADE"), primary_key=True
    )
    size: Mapped[int]
    # Files in the folder and below; null when the engine does not count files (du).
    files: Mapped[int | None]
    # Folders below this one, at any depth.
    dirs: Mapped[int]


class CapacitySample(Base):
    __tablename__ = "capacity_samples"
    __table_args__ = (Index("ix_capacity_directory_time", "directory_id", "measured_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    directory_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("directories.id", ondelete="CASCADE")
    )
    measured_at: Mapped[datetime] = mapped_column(default=utcnow)
    total: Mapped[int]
    used: Mapped[int]
    free: Mapped[int]
    # Identifies the volume (st_dev), to tell which directories share one.
    device: Mapped[str] = mapped_column(String(40))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    # SHA-256 of the session token: the database alone never holds a usable cookie.
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]


class PushSubscription(Base):
    """A browser, or an app added to a Home Screen, that turned notifications on: where its push
    service delivers them, its keys, and what it wants to be told."""

    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The address of this browser at its push service (a capability: whoever has it can send).
    endpoint: Mapped[str] = mapped_column(String(2048), unique=True)
    p256dh: Mapped[str] = mapped_column(String(100))
    auth: Mapped[str] = mapped_column(String(40))
    # The address DiskSprawl was opened at on the device: the pages its notifications open.
    origin: Mapped[str] = mapped_column(String(300))
    # Language of its notifications: the interface's, en or es.
    locale: Mapped[str] = mapped_column(String(2))
    # With a password, the digest of the session that registered it (as auth_sessions.token_hash);
    # signing out there sets it to None, which pauses its notifications until it signs in again.
    session_hash: Mapped[str | None] = mapped_column(String(64))
    notify_manual_scans: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_scheduled_scans: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_failed_scans: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_capacity: Mapped[bool] = mapped_column(Boolean, default=True)
    # The level of each volume (by device id) this browser was last told about: "warning" or
    # "critical". A volume that is not listed is below the warning threshold.
    capacity_levels: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    # Notifications its push service refused in a row.
    failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Last time the device registered (each time DiskSprawl opens there).
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class PushMessage(Base):
    """A notification still to send to one device: the end of a scan, or a volume that filled."""

    __tablename__ = "push_messages"
    __table_args__ = (
        Index("ix_push_messages_subscription", "subscription_id"),
        Index("ix_push_messages_scan", "scan_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    subscription_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("push_subscriptions.id", ondelete="CASCADE")
    )
    # scan_completed, scan_failed or capacity.
    kind: Mapped[str] = mapped_column(String(20))
    scan_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("scans.id", ondelete="CASCADE")
    )
    # What a capacity notification tells: the level, the directories on the volume, the use.
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


def create_db_engine(path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"check_same_thread": False, "timeout": 30},
        # SQL parameters hold folder names: keep them out of error messages and logs.
        hide_parameters=True,
    )

    @event.listens_for(engine, "connect")
    def configure(connection: Any, _record: Any) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def transaction(sessions: sessionmaker[Session]) -> Iterator[Session]:
    with sessions() as session, session.begin():
        yield session


def migrate(path: Path) -> None:
    """Brings the database at `path` to the latest schema (creating it if needed)."""
    from alembic import command
    from alembic.config import Config

    path.parent.mkdir(parents=True, exist_ok=True)
    config = Config()
    config.set_main_option("script_location", "disksprawl:migrations")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{path}")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
