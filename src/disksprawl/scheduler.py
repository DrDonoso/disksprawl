"""Scheduled work with APScheduler: each directory's cron, the capacity measurements and the expiry
of scan logs."""

import logging
import threading
from datetime import datetime, timedelta

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from . import capacity, cron, settings_store
from .config import Settings
from .db import AuthSession, CapacitySample, Directory, Scan, transaction, utcnow
from .scanning import Scanner

log = logging.getLogger(__name__)

# A run missed while the app was down, or while it was busy, still starts within this window.
GRACE = timedelta(hours=1)
CAPACITY_JOB = "capacity"
# Scan logs older than the `scan_log_days` setting are deleted at startup and then every hour.
LOGS_JOB = "scan-logs"
LOGS_INTERVAL_HOURS = 1
# Capacity samples older than this are deleted (the history chart shows a year at most).
CAPACITY_RETENTION = timedelta(days=400)
# A scheduled scan that cannot be queued because the database is busy is tried again, this many
# times, this many seconds apart, instead of being lost.
ENQUEUE_ATTEMPTS = 3
ENQUEUE_RETRY_SECONDS = 10.0


def scan_job_id(directory_id: int) -> str:
    return f"scan-{directory_id}"


class Scheduler:
    # Tests and the browser tests' server start it paused: next runs are still computed, but no
    # schedule fires in the middle of a test because of the time of day it runs at.
    paused = False

    def __init__(self, sessions: sessionmaker[Session], config: Settings, scanner: Scanner):
        self.sessions = sessions
        self.config = config
        self.scanner = scanner
        self.scheduler = BackgroundScheduler(
            timezone=config.zone,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": int(GRACE.total_seconds()),
            },
        )
        self._stopping = threading.Event()

    def start(self) -> None:
        self.scheduler.start(paused=self.paused)
        with self.sessions() as db:
            directories = db.scalars(select(Directory)).all()
            for directory in directories:
                self.sync(directory)
            minutes = settings_store.load(db)["capacity_interval_minutes"]
        self.schedule_capacity(minutes, run_now=True)
        self.scheduler.add_job(
            self.sweep_logs,
            IntervalTrigger(hours=LOGS_INTERVAL_HOURS, timezone=self.config.zone),
            id=LOGS_JOB,
            replace_existing=True,
            next_run_time=utcnow(),
        )
        self.catch_up()

    def shutdown(self) -> None:
        self._stopping.set()
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def sync(self, directory: Directory) -> None:
        """Adds, replaces or removes the cron job of a directory after it changed."""
        job_id = scan_job_id(directory.id)
        if not directory.enabled or not directory.cron:
            self.remove(directory.id)
            return
        self.scheduler.add_job(
            self._scheduled_scan,
            cron.trigger(directory.cron, self.config.zone),
            id=job_id,
            args=[directory.id],
            replace_existing=True,
        )

    def remove(self, directory_id: int) -> None:
        try:
            self.scheduler.remove_job(scan_job_id(directory_id))
        except JobLookupError:
            pass

    def next_run(self, directory_id: int) -> datetime | None:
        job = self.scheduler.get_job(scan_job_id(directory_id))
        return job.next_run_time if job is not None else None

    def schedule_capacity(self, minutes: int, *, run_now: bool = False) -> None:
        self.scheduler.add_job(
            self.measure_capacity,
            IntervalTrigger(minutes=minutes, timezone=self.config.zone),
            id=CAPACITY_JOB,
            replace_existing=True,
            **({"next_run_time": utcnow()} if run_now else {}),
        )

    def catch_up(self) -> None:
        """Queues the scans whose scheduled time passed in the last hour while the app was down.

        A failure is logged: it never stops the app from starting."""
        now = utcnow()
        due: list[int] = []
        try:
            with self.sessions() as db:
                directories = db.scalars(
                    select(Directory).where(
                        Directory.enabled.is_(True), Directory.cron.is_not(None)
                    )
                ).all()
                for directory in directories:
                    last = db.scalar(
                        select(func.max(Scan.queued_at)).where(Scan.directory_id == directory.id)
                    )
                    since = max(last, now - GRACE) if last else now - GRACE
                    if directory.cron and cron.missed_run(
                        directory.cron, self.config.zone, since, now
                    ):
                        due.append(directory.id)
        except Exception:
            log.exception("Missed scheduled scans could not be checked")
            return
        for directory_id in due:
            log.info("Queuing a missed scheduled scan of directory %s", directory_id)
            try:
                self.scanner.enqueue(directory_id, "scheduled")
            except Exception:
                log.exception("The missed scan of directory %s could not be queued", directory_id)

    def _scheduled_scan(self, directory_id: int) -> None:
        for attempt in range(1, ENQUEUE_ATTEMPTS + 1):
            try:
                with self.sessions() as db:
                    directory = db.get(Directory, directory_id)
                    if directory is None or not directory.enabled:
                        return
                self.scanner.enqueue(directory_id, "scheduled")
                return
            except OperationalError:
                if attempt == ENQUEUE_ATTEMPTS:
                    raise
                log.warning(
                    "The database is busy: the scheduled scan of directory %s waits %s s",
                    directory_id,
                    ENQUEUE_RETRY_SECONDS,
                )
                if self._stopping.wait(ENQUEUE_RETRY_SECONDS):
                    return

    def sweep_logs(self) -> None:
        """Deletes the scan logs that expired. A failure is logged; the next hour tries again."""
        try:
            with self.sessions() as db:
                days = settings_store.load(db)["scan_log_days"]
            self.scanner.sweep_logs(days)
        except Exception:
            log.exception("Expired scan logs could not be deleted")

    def measure_directory(self, directory_id: int) -> None:
        with self.sessions() as db:
            directory = db.get(Directory, directory_id)
            path = directory.path if directory is not None else None
        if path is None:
            return
        # Measured outside any transaction: a slow volume must not hold the database.
        volume = capacity.measure_folder(path, self.config.scan_root, directory_id)
        if volume is None:
            return
        with transaction(self.sessions) as db:
            if db.get(Directory, directory_id) is not None:
                capacity.store(db, directory_id, volume)

    def measure_capacity(self) -> None:
        try:
            with self.sessions() as db:
                directories = db.execute(select(Directory.id, Directory.path)).all()
            # Each volume is measured outside any transaction, with a time limit: a slow or hung
            # network share must neither hold the database nor stop the other measurements.
            measured = []
            for directory_id, path in directories:
                volume = capacity.measure_folder(path, self.config.scan_root, directory_id)
                if volume is not None:
                    measured.append((directory_id, volume))
            with transaction(self.sessions) as db:
                existing = set(db.scalars(select(Directory.id)))
                for directory_id, volume in measured:
                    if directory_id in existing:
                        capacity.store(db, directory_id, volume)
                db.execute(
                    delete(CapacitySample).where(
                        CapacitySample.measured_at < utcnow() - CAPACITY_RETENTION
                    )
                )
                db.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
        except Exception:
            log.exception("Capacity could not be measured")
