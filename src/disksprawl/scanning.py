"""The scan queue: one scan at a time, in order of arrival, each in a worker subprocess.

Scans wait in the database (`queued`), so they survive a restart; a scan that was running when the
app stopped becomes `interrupted`. While a scan runs, its live counters stay in memory and the API
reads them, with an estimate based on the previous completed scan of the same directory.
"""

import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from . import capacity, settings_store
from .config import Settings
from .db import Directory, PathNode, Scan, ScanEntry, transaction, utcnow
from .engines import EngineInfo
from .engines.common import Job, read_result
from .errors import AppError
from .paths import resolve

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
MIB = 1024 * 1024
# Seconds a cancelled worker gets to stop before it is killed.
STOP_GRACE = 5.0


def options_of(directory: Directory) -> dict[str, Any]:
    return {
        "engine": directory.engine,
        "threads": directory.threads,
        "pace": directory.pace,
        "low_priority": directory.low_priority,
        "measure": directory.measure,
        "one_filesystem": directory.one_filesystem,
        "excludes": list(directory.excludes),
    }


@dataclass
class Previous:
    """The last completed scan of the directory, the reference for estimates."""

    total_bytes: int | None
    dir_count: int | None
    duration: float | None
    measure: str


@dataclass
class Live:
    scan_id: int
    directory_id: int
    engine: str
    measure: str
    started: float = field(default_factory=time.monotonic)
    phase: str = "scanning"
    counters: bool = True
    dirs: int = 0
    files: int | None = None
    done_bytes: int = 0
    current: str | None = None
    previous: Previous | None = None
    process: subprocess.Popen[bytes] | None = None
    cancel: bool = False

    def estimate(self, elapsed: float) -> tuple[float | None, float | None]:
        """(share done, seconds left), both approximate; None when they cannot be estimated."""
        previous = self.previous
        if not self.counters or previous is None or self.phase != "scanning":
            return None, None
        ratio = None
        # du reports finished folders only: its bytes lag behind, its folder count does not.
        if (
            self.engine != "du"
            and previous.total_bytes
            and previous.measure == self.measure
            and self.done_bytes > 0
        ):
            ratio = self.done_bytes / previous.total_bytes
        elif self.dirs and previous.dir_count:
            ratio = self.dirs / previous.dir_count
        if ratio is None:
            return None, None
        ratio = min(max(ratio, 0.0), 0.99)
        remaining = elapsed * (1 - ratio) / ratio if ratio >= 0.01 and elapsed >= 2 else None
        return ratio, remaining


class Scanner:
    def __init__(
        self,
        sessions: sessionmaker[Session],
        config: Settings,
        engines: Callable[[], dict[str, EngineInfo]],
        *,
        on_finished: Callable[[int], None] | None = None,
    ):
        self.sessions = sessions
        self.config = config
        self.engines = engines
        self.on_finished = on_finished
        self.work_dir = config.data_dir / "work"
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._cancel_requests: set[int] = set()
        self._live: Live | None = None
        self._thread: threading.Thread | None = None
        # The scan worker; tests replace it with a scripted one.
        self.worker_command = [sys.executable, "-m", "disksprawl.engines"]

    # Lifecycle

    def start(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        for leftover in self.work_dir.iterdir():
            leftover.unlink(missing_ok=True)
        with transaction(self.sessions) as db:
            for scan in db.scalars(select(Scan).where(Scan.status == "running")):
                self._close(scan, "interrupted", "interrupted")
        self._thread = threading.Thread(target=self._loop, name="scanner", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15) -> None:
        self._stopping.set()
        self._wake.set()
        live = self._live
        if live is not None:
            live.cancel = True
            self._terminate(live)
        if self._thread is not None:
            self._thread.join(timeout)

    # Requests from the API and the scheduler

    def enqueue(self, directory_id: int, trigger: str) -> int:
        """Queues a scan of the directory, unless one is already queued or running (its id)."""
        with self._lock, transaction(self.sessions) as db:
            existing = db.scalars(
                select(Scan.id)
                .where(Scan.directory_id == directory_id, Scan.status.in_(ACTIVE))
                .order_by(Scan.id)
            ).first()
            if existing is not None:
                return existing
            directory = db.get(Directory, directory_id)
            if directory is None:
                raise AppError("directory_not_found", 404)
            scan = Scan(
                directory_id=directory_id,
                status="queued",
                trigger=trigger,
                engine=directory.engine,
                options=options_of(directory),
                queued_at=utcnow(),
                error_count=0,
                error_sample=[],
                has_detail=False,
            )
            db.add(scan)
            db.flush()
            scan_id = scan.id
        self._wake.set()
        return scan_id

    def cancel(self, scan_id: int) -> None:
        with self._lock, transaction(self.sessions) as db:
            scan = db.get(Scan, scan_id)
            if scan is None:
                raise AppError("scan_not_found", 404)
            if scan.status == "queued":
                self._close(scan, "cancelled", None)
                return
            if scan.status != "running":
                raise AppError("scan_not_active", 409)
            self._cancel_requests.add(scan_id)
        live = self._live
        if live is not None and live.scan_id == scan_id:
            live.cancel = True
            self._terminate(live)

    def live(self) -> Live | None:
        return self._live

    def queue_positions(self, db: Session) -> dict[int, int]:
        """Position in the queue (1 is next) of every queued scan."""
        queued = db.scalars(
            select(Scan.id).where(Scan.status == "queued").order_by(Scan.queued_at, Scan.id)
        ).all()
        return {scan_id: number for number, scan_id in enumerate(queued, start=1)}

    # The worker thread

    def _loop(self) -> None:
        while not self._stopping.is_set():
            try:
                scan_id = self._claim()
            except Exception:
                log.exception("The scan queue could not be read")
                scan_id = None
            if scan_id is None:
                self._wake.wait(5)
                self._wake.clear()
                continue
            try:
                self._run(scan_id)
            except Exception:
                log.exception("Scan %s failed unexpectedly", scan_id)
                self._fail(scan_id, "internal_error")
            finally:
                self._live = None
                self._cancel_requests.discard(scan_id)
                if self.on_finished is not None:
                    try:
                        self.on_finished(scan_id)
                    except Exception:
                        log.exception("After-scan hook failed")

    def _claim(self) -> int | None:
        with self._lock, transaction(self.sessions) as db:
            scan = db.scalars(
                select(Scan).where(Scan.status == "queued").order_by(Scan.queued_at, Scan.id)
            ).first()
            if scan is None:
                return None
            scan.status = "running"
            scan.started_at = utcnow()
            return scan.id

    def _close(self, scan: Scan, status: str, message: str | None) -> None:
        now = utcnow()
        scan.status = status
        scan.finished_at = now
        scan.message = message[:1000] if message else None
        if scan.started_at is not None:
            scan.duration_seconds = round((now - scan.started_at).total_seconds(), 3)

    def _fail(self, scan_id: int, message: str, status: str = "failed") -> None:
        with transaction(self.sessions) as db:
            scan = db.get(Scan, scan_id)
            if scan is not None and scan.status == "running":
                self._close(scan, status, message)

    def _terminate(self, live: Live) -> None:
        process = live.process
        if process is None or process.poll() is not None:
            return
        for number in (signal.SIGCONT, signal.SIGTERM):
            try:
                os.killpg(process.pid, number)
            except ProcessLookupError:
                return

    def _command(self, job_path: Path, low_priority: bool) -> list[str]:
        command = [*self.worker_command, str(job_path)]
        if not low_priority:
            return command
        prefix: list[str] = []
        if nice := shutil.which("nice"):
            prefix += [nice, "-n", "19"]
        if ionice := shutil.which("ionice"):
            # The idle I/O class; -t carries on where the kernel or a container refuses it.
            prefix += [ionice, "-c", "3", "-t"]
        return prefix + command

    def _run(self, scan_id: int) -> None:
        with self.sessions() as db:
            scan = db.get(Scan, scan_id)
            if scan is None:
                return
            directory = db.get(Directory, scan.directory_id)
            if directory is None:
                return
            options = dict(scan.options)
            path = directory.path
            directory_id = directory.id
            min_mib = settings_store.load(db)["min_entry_mib"]
            reference = db.scalars(
                select(Scan)
                .where(Scan.directory_id == directory_id, Scan.status == "completed")
                .order_by(Scan.finished_at.desc(), Scan.id.desc())
            ).first()
            previous = (
                Previous(
                    reference.total_bytes,
                    reference.dir_count,
                    reference.duration_seconds,
                    str(reference.options.get("measure", "apparent")),
                )
                if reference is not None
                else None
            )
        engine_id = str(options["engine"])
        live = Live(
            scan_id,
            directory_id,
            engine_id,
            str(options["measure"]),
            previous=previous,
            counters=engine_id != "gdu",
            files=None if engine_id == "du" else 0,
        )
        self._live = live
        if scan_id in self._cancel_requests or self._stopping.is_set():
            self._fail(scan_id, "", "interrupted" if self._stopping.is_set() else "cancelled")
            return
        try:
            root = resolve(path, self.config.scan_root)
        except AppError as error:
            self._fail(scan_id, error.code)
            return
        engine = self.engines().get(engine_id)
        if engine is None or not engine.available:
            self._fail(scan_id, "engine_unavailable")
            return
        threads = max(1, min(int(options["threads"]), engine.max_threads))
        job_path = self.work_dir / f"scan-{scan_id}.json"
        result_path = self.work_dir / f"scan-{scan_id}.jsonl"
        Job(
            engine=engine_id,
            root=str(root),
            result_path=str(result_path),
            threads=threads,
            pace=str(options["pace"]),
            measure=str(options["measure"]),
            one_filesystem=bool(options["one_filesystem"]),
            excludes=list(options["excludes"]),
            min_entry_bytes=min_mib * MIB,
            binary=engine.binary,
        ).dump(str(job_path))
        try:
            outcome, detail = self._work(live, job_path, bool(options["low_priority"]))
            if live.cancel:
                self._fail(scan_id, "", "interrupted" if self._stopping.is_set() else "cancelled")
            elif outcome is None:
                self._fail(scan_id, detail or "scan_failed")
            else:
                live.phase = "saving"
                self._store(scan_id, directory_id, root, result_path, outcome)
        finally:
            for leftover in (job_path, result_path):
                leftover.unlink(missing_ok=True)

    def _work(
        self, live: Live, job_path: Path, low_priority: bool
    ) -> tuple[dict[str, Any] | None, str]:
        """Runs the worker; (its `done` message, '') or (None, why it failed)."""
        process = subprocess.Popen(
            self._command(job_path, low_priority),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        live.process = process
        if live.cancel:
            self._terminate(live)
        tail: list[str] = []

        def read_stderr() -> None:
            assert process.stderr is not None
            for raw in process.stderr:
                tail.append(raw.decode("utf-8", "replace").rstrip())
                del tail[:-20]

        reader = threading.Thread(target=read_stderr, name="worker-stderr", daemon=True)
        reader.start()
        done: dict[str, Any] | None = None
        failure = ""
        assert process.stdout is not None
        for raw in process.stdout:
            try:
                message = json.loads(raw)
            except ValueError:
                continue
            kind = message.get("type")
            if kind == "progress":
                live.phase = str(message.get("phase", "scanning"))
                live.counters = bool(message.get("live", True))
                live.dirs = int(message.get("dirs") or 0)
                files = message.get("files")
                live.files = int(files) if files is not None else None
                live.done_bytes = int(message.get("bytes") or 0)
                live.current = message.get("current")
            elif kind == "done":
                done = message
            elif kind == "error":
                failure = str(message.get("code", "scan_failed"))
                detail = message.get("detail")
                if detail:
                    failure += f": {detail}"
        deadline = time.monotonic() + STOP_GRACE
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        reader.join(timeout=5)
        if done is not None and process.returncode == 0:
            return done, ""
        if not failure:
            details = " | ".join(line for line in tail[-3:] if line)
            failure = f"scan_failed: exit status {process.returncode}" + (
                f": {details}" if details else ""
            )
            if tail:
                log.warning("Scan worker %s failed:\n%s", live.scan_id, "\n".join(tail))
        return None, failure

    def _store(
        self, scan_id: int, directory_id: int, root: Path, result_path: Path, done: dict[str, Any]
    ) -> None:
        with transaction(self.sessions) as db:
            scan = db.get(Scan, scan_id)
            if scan is None or scan.status != "running":
                return
            import_entries(db, scan_id, directory_id, result_path)
            scan.total_bytes = int(done["total_bytes"])
            file_count = done.get("file_count")
            scan.file_count = int(file_count) if file_count is not None else None
            scan.dir_count = int(done["dir_count"])
            scan.error_count = int(done.get("error_count") or 0)
            scan.error_sample = list(done.get("error_sample") or [])
            scan.has_detail = True
            try:
                volume = capacity.measure(root)
            except OSError:
                volume = None
            if volume is not None:
                scan.capacity_total = volume.total
                scan.capacity_used = volume.used
                scan.capacity_free = volume.free
            self._close(scan, "completed", None)
            directory = db.get(Directory, directory_id)
            if directory is not None:
                capacity.sample(db, directory, self.config.scan_root)
            apply_retention(db, directory_id, settings_store.load(db)["keep_scans"])


def import_entries(db: Session, scan_id: int, directory_id: int, result_path: Path) -> int:
    """Stores the folders of a finished scan, reusing the path ids of earlier scans."""
    cursor = db.connection().connection.cursor()
    known: dict[tuple[int | None, str], int] = {
        (parent, name): identifier
        for identifier, parent, name in cursor.execute(
            "SELECT id, parent_id, name FROM paths WHERE directory_id = ?", (directory_id,)
        )
    }
    path_ids: dict[int, int] = {}
    rows: list[tuple[int, int, int, int | None, int]] = []
    for index, parent, name, size, files, dirs in read_result(str(result_path)):
        parent_id = path_ids[parent] if parent >= 0 else None
        path_id = known.get((parent_id, name))
        if path_id is None:
            cursor.execute(
                "INSERT INTO paths (directory_id, parent_id, name) VALUES (?, ?, ?)",
                (directory_id, parent_id, name),
            )
            path_id = int(cursor.lastrowid or 0)
            known[(parent_id, name)] = path_id
        path_ids[index] = path_id
        rows.append((scan_id, path_id, size, files, dirs))
    cursor.executemany(
        "INSERT INTO scan_entries (scan_id, path_id, size, files, dirs) VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    return len(rows)


def apply_retention(db: Session, directory_id: int, keep: int) -> None:
    """Keeps the folders of the latest `keep` completed scans; older scans keep their summary."""
    detailed = db.scalars(
        select(Scan.id)
        .where(Scan.directory_id == directory_id, Scan.has_detail.is_(True))
        .order_by(Scan.finished_at.desc(), Scan.id.desc())
    ).all()
    expired = list(detailed[keep:])
    if not expired:
        return
    db.execute(delete(ScanEntry).where(ScanEntry.scan_id.in_(expired)))
    db.execute(update(Scan).where(Scan.id.in_(expired)).values(has_detail=False))
    referenced = (
        select(ScanEntry.path_id)
        .join(Scan, Scan.id == ScanEntry.scan_id)
        .where(Scan.directory_id == directory_id)
    )
    db.execute(
        delete(PathNode).where(
            PathNode.directory_id == directory_id, PathNode.id.not_in(referenced)
        )
    )


def elapsed_since(moment: datetime | None) -> float:
    return max((utcnow() - moment).total_seconds(), 0.0) if moment else 0.0
