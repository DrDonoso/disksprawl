"""Scheduled work with APScheduler: each directory's cron and the capacity measurements."""

import logging
from datetime import datetime, timedelta

from apscheduler.jobstores.base import JobLookupError
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from . import capacity, cron, settings_store
from .config import Settings
from .db import AuthSession, CapacitySample, Directory, Scan, transaction, utcnow
from .scanning import Scanner

log = logging.getLogger(__name__)

# A run missed while the app was down, or while it was busy, still starts within this window.
GRACE = timedelta(hours=1)
CAPACITY_JOB = "capacity"
# Capacity samples older than this are deleted (the history chart shows a year at most).
CAPACITY_RETENTION = timedelta(days=400)


def scan_job_id(directory_id: int) -> str:
    return f"scan-{directory_id}"


class Scheduler:
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

    def start(self) -> None:
        self.scheduler.start()
        with self.sessions() as db:
            directories = db.scalars(select(Directory)).all()
            for directory in directories:
                self.sync(directory)
            minutes = settings_store.load(db)["capacity_interval_minutes"]
        self.schedule_capacity(minutes, run_now=True)
        self.catch_up()

    def shutdown(self) -> None:
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
        """Queues the scans whose scheduled time passed in the last hour while the app was down."""
        now = utcnow()
        with self.sessions() as db:
            directories = db.scalars(
                select(Directory).where(Directory.enabled.is_(True), Directory.cron.is_not(None))
            ).all()
            for directory in directories:
                last = db.scalar(
                    select(func.max(Scan.queued_at)).where(Scan.directory_id == directory.id)
                )
                since = max(last, now - GRACE) if last else now - GRACE
                if directory.cron and cron.missed_run(directory.cron, self.config.zone, since, now):
                    log.info("Queuing a missed scheduled scan of directory %s", directory.id)
                    self.scanner.enqueue(directory.id, "scheduled")

    def _scheduled_scan(self, directory_id: int) -> None:
        with self.sessions() as db:
            directory = db.get(Directory, directory_id)
            if directory is None or not directory.enabled:
                return
        self.scanner.enqueue(directory_id, "scheduled")

    def measure_directory(self, directory_id: int) -> None:
        with transaction(self.sessions) as db:
            directory = db.get(Directory, directory_id)
            if directory is not None:
                capacity.sample(db, directory, self.config.scan_root)

    def measure_capacity(self) -> None:
        try:
            with transaction(self.sessions) as db:
                for directory in db.scalars(select(Directory)).all():
                    capacity.sample(db, directory, self.config.scan_root)
                db.execute(
                    delete(CapacitySample).where(
                        CapacitySample.measured_at < utcnow() - CAPACITY_RETENTION
                    )
                )
                db.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
        except Exception:
            log.exception("Capacity could not be measured")
