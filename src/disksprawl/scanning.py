"""The scan queue: one scan at a time, in order of arrival, each in a worker subprocess.

Scans wait in the database (`queued`), so they survive a restart; a scan that was running when the
app stopped becomes `interrupted`. While a scan runs, its live counters stay in memory and the API
reads them, with an estimate from the folders and files of the previous completed scan of the same
directory. Each scan that starts writes its log file (scan_logs.py).
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

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from . import capacity, retention, scan_logs, settings_store
from .config import Settings
from .db import Directory, Scan, transaction, utcnow
from .engines import EngineInfo
from .engines.common import Job, clean, read_result
from .errors import AppError
from .paths import is_text, resolve
from .scan_logs import ScanLog, ScanLogs

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
MIB = 1024 * 1024
# Scans whose expired log is forgotten in each transaction.
LOG_BATCH = 500
# Seconds a cancelled worker gets to stop before it is killed.
STOP_GRACE = 5.0
# Storing a finished scan is tried this many times while the database stays busy (each attempt
# already waits for SQLite's busy timeout), with these seconds between attempts, doubling.
STORE_ATTEMPTS = 4
RETRY_SECONDS = 1.0
# Longest wait between two attempts to record how a scan ended.
MAX_RETRY_SECONDS = 60.0
# For estimates: listing a folder costs about as much time as reading the details of ten files.
FOLDER_COST = 10


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

    dir_count: int | None
    file_count: int | None
    duration: float | None


@dataclass
class Live:
    scan_id: int
    directory_id: int
    engine: str
    measure: str
    started: float = field(default_factory=time.monotonic)
    phase: str = "scanning"
    counters: bool = True
    # Folders and files read so far, the scanned folder included. Files are None when they are not
    # counted (du); folders are None when they are counted with the files (ncdu with several
    # threads draws a single counter).
    dirs: int | None = 0
    files: int | None = None
    done_bytes: int = 0
    current: str | None = None
    previous: Previous | None = None
    process: subprocess.Popen[bytes] | None = None
    cancel: bool = False
    log: ScanLog | None = None

    def estimate(self, elapsed: float) -> tuple[float | None, float | None]:
        """(share done, seconds left), both approximate; None when they cannot be estimated.

        The share compares the work done so far with the previous scan's: its files plus
        FOLDER_COST times its folders. Never the bytes: a scan reads each entry's details, which
        takes as long for a large video as for a small text file, so a scan that meets its largest
        files first reads most of the bytes long before it ends. Folders weigh more than files
        because each one is listed, with several requests over a network share, but they are
        counted only once their listing ends: weighing them by their number, rather than giving
        them a fixed half of the estimate, keeps a folder of many files and no subfolders from
        stalling at 50 %. The seconds left follow the pace of this scan so far.
        """
        previous = self.previous
        if not self.counters or previous is None or self.phase != "scanning":
            return None, None
        dir_count, file_count = previous.dir_count, previous.file_count
        if dir_count is None:
            return None, None
        # dir_count leaves out the scanned folder itself, which the counters include.
        folders = dir_count + 1
        if self.dirs is None:
            # One counter of files and folders together: compared with both.
            if self.files is None or file_count is None:
                return None, None
            done, total = self.files, file_count + folders
        elif self.files is None or file_count is None:
            # du counts no files, neither while it scans nor as the previous scan: folders only.
            done, total = self.dirs, folders
        else:
            done = self.files + FOLDER_COST * self.dirs
            total = file_count + FOLDER_COST * folders
        # Nothing counted yet: nothing to estimate from.
        if not done:
            return None, None
        ratio = min(done / total, 0.99)
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
        self.logs = ScanLogs(config.logs_dir, config.zone)
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._lock = threading.Lock()
        self._cancel_requests: set[int] = set()
        self._live: Live | None = None
        # The scan the worker thread claimed: any other scan marked running was left so by a
        # failure.
        self._current: int | None = None
        # Storing a scan, pruning old ones and deleting a directory never overlap: they share paths.
        self._storage_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        # The scan worker; tests replace it with a scripted one.
        self.worker_command = [sys.executable, "-m", "disksprawl.engines"]

    # Lifecycle

    def start(self) -> None:
        self.work_dir.mkdir(parents=True, exist_ok=True)
        for leftover in self.work_dir.iterdir():
            leftover.unlink(missing_ok=True)
        with transaction(self.sessions) as db:
            interrupted = list(db.scalars(select(Scan).where(Scan.status == "running")))
            for scan in interrupted:
                self._close(scan, "interrupted", "interrupted")
        # Their logs end too, once the transaction is over.
        for scan in interrupted:
            if scan.log_file is not None:
                scan_log = self.logs.reopen(scan.id, scan.log_file)
                try:
                    scan_log.end(scan)
                except Exception:
                    log.exception("The log of scan %s could not be ended", scan.id)
                finally:
                    scan_log.close()
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
            for existing in db.scalars(
                select(Scan)
                .where(Scan.directory_id == directory_id, Scan.status.in_(ACTIVE))
                .order_by(Scan.id)
            ):
                if self._stale(existing):
                    self._close(existing, "failed", "internal_error")
                    continue
                return existing.id
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
            if scan.status == "queued" or self._stale(scan):
                # Nothing runs it: a queued scan, or one a failure left marked as running.
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

    def prune(self, directory_id: int, keep: int) -> None:
        """Keeps the folders of the latest `keep` completed scans of a directory (in batches)."""
        with self._storage_lock:
            retention.prune(self.sessions, directory_id, keep)

    def sweep_logs(self, days: int) -> int:
        """Deletes the logs of the scans that ended more than `days` days ago; how many. Each
        batch of scans forgets its logs before the files go: a failure deletes nothing they
        still point to, and the next sweep finds the files again."""
        with self.sessions() as db:
            keep = {
                name
                for name in db.scalars(
                    select(Scan.log_file).where(
                        Scan.status.in_(ACTIVE), Scan.log_file.is_not(None)
                    )
                )
                if name
            }
        # The scan the worker thread runs keeps its log until it has written its end, even once
        # its record says it ended. Read after the query: a scan that was live then still is, or
        # has written its end since.
        live = self._live
        if live is not None and live.log is not None and live.log.relative:
            keep.add(live.log.relative)
        expired = self.logs.expired(days, keep, time.time())
        for start in range(0, len(expired), LOG_BATCH):
            batch = expired[start : start + LOG_BATCH]
            with transaction(self.sessions) as db:
                db.execute(
                    update(Scan)
                    .where(Scan.log_file.in_([name for name in batch if is_text(name)]))
                    .values(log_file=None)
                )
            self.logs.delete(batch)
        self.logs.remove_empty_folders()
        return len(expired)

    def forget(self, directory_id: int) -> None:
        """Deletes a directory and everything stored about it (never its files): its queued and
        running scans stop first, then its records go in batches, and its scans' logs."""
        with self._lock, transaction(self.sessions) as db:
            for scan in db.scalars(
                select(Scan).where(Scan.directory_id == directory_id, Scan.status.in_(ACTIVE))
            ):
                if scan.status == "queued" or self._stale(scan):
                    self._close(scan, "cancelled", None)
                else:
                    self._cancel_requests.add(scan.id)
        live = self._live
        if live is not None and live.directory_id == directory_id:
            live.cancel = True
            self._terminate(live)
        with self._storage_lock:
            with self.sessions() as db:
                logs = [
                    name
                    for name in db.scalars(
                        select(Scan.log_file).where(Scan.directory_id == directory_id)
                    )
                    if name
                ]
            retention.delete_directory(self.sessions, directory_id)
        self.logs.delete(logs)

    def _stale(self, scan: Scan) -> bool:
        """A scan marked running that the worker thread is not running (call with `_lock` held)."""
        return scan.status == "running" and scan.id != self._current

    def queue_positions(self, db: Session) -> dict[int, int]:
        """Position in the queue (1 is next) of every queued scan."""
        queued = db.scalars(
            select(Scan.id).where(Scan.status == "queued").order_by(Scan.queued_at, Scan.id)
        ).all()
        return {scan_id: number for number, scan_id in enumerate(queued, start=1)}

    # The worker thread

    def _loop(self) -> None:
        # Nothing may end this thread but stop(): without it the queue would wait for a restart.
        while not self._stopping.is_set():
            try:
                self._next()
            except Exception:
                log.exception("The scan queue failed; trying again")
                self._stopping.wait(5)

    def _next(self) -> None:
        self._wake.clear()
        scan_id = self._claim()
        if scan_id is None:
            self._wake.wait(5)
            return
        try:
            self._run(scan_id)
        except Exception:
            log.exception("Scan %s failed unexpectedly", scan_id)
            self._fail(scan_id, "internal_error")
        finally:
            live = self._live
            if live is not None and live.log is not None and live.log.open:
                self._end_log(scan_id, live.log)
            with self._lock:
                self._live = None
                self._current = None
                self._cancel_requests.discard(scan_id)
            if self.on_finished is not None:
                try:
                    self.on_finished(scan_id)
                except Exception:
                    log.exception("After-scan hook failed")

    def _claim(self) -> int | None:
        with self._lock:
            with transaction(self.sessions) as db:
                # No scan runs between two claims: one still marked running was left so when
                # recording its end failed.
                for stale in db.scalars(select(Scan).where(Scan.status == "running")):
                    log.warning("Scan %s was left running; it is recorded as failed", stale.id)
                    self._close(stale, "failed", "internal_error")
                scan = db.scalars(
                    select(Scan).where(Scan.status == "queued").order_by(Scan.queued_at, Scan.id)
                ).first()
                if scan is None:
                    return None
                scan.status = "running"
                scan.started_at = utcnow()
                claimed = scan.id
            self._current = claimed
            return claimed

    def _close(self, scan: Scan, status: str, message: str | None) -> None:
        now = utcnow()
        scan.status = status
        scan.finished_at = now
        scan.message = message[:1000] if message else None
        if scan.started_at is not None:
            scan.duration_seconds = round((now - scan.started_at).total_seconds(), 3)

    def _fail(self, scan_id: int, message: str, status: str = "failed") -> None:
        """Records how a scan ended. While the database fails (busy for longer than its timeout,
        for example) it tries again, and the queue waits: the scan never stays running."""
        delay = RETRY_SECONDS
        while True:
            try:
                with transaction(self.sessions) as db:
                    scan = db.get(Scan, scan_id)
                    if scan is not None and scan.status == "running":
                        self._close(scan, status, message)
                return
            except Exception:
                log.exception("Scan %s could not be recorded as %s; trying again", scan_id, status)
                if self._stopping.wait(delay):
                    return
                delay = min(delay * 2, MAX_RETRY_SECONDS)

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

    def _open_log(
        self, about: scan_logs.About, engine: EngineInfo | None, min_mib: int
    ) -> ScanLog:
        """Creates the scan's log, with its header, and records its name (outside any write
        transaction but that short one). Whatever fails here, the scan runs all the same."""
        scan_log = self.logs.create(about)
        if not scan_log.open:
            return scan_log
        try:
            scan_log.write(*scan_logs.header(about, engine, min_mib, self.config.image_tag))
            scan_log.event("Queued", about.queued_at)
            scan_log.event("Started", about.started_at)
            with transaction(self.sessions) as db:
                db.execute(
                    update(Scan).where(Scan.id == about.scan_id).values(log_file=scan_log.relative)
                )
        except Exception:
            # Unrecorded, the log expires like any other.
            log.exception("The log of scan %s could not be started", about.scan_id)
        return scan_log

    def _end_log(self, scan_id: int, scan_log: ScanLog) -> None:
        """Writes how the scan ended, and closes its log. A scan still marked running (the app is
        stopping and could not record its end) gets its end when the app starts again."""
        try:
            with self.sessions() as db:
                scan = db.get(Scan, scan_id)
            if scan is not None and scan.status not in ACTIVE:
                scan_log.end(scan)
        except Exception:
            log.exception("The log of scan %s could not be ended", scan_id)
        finally:
            scan_log.close()

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
            about = scan_logs.About(
                scan_id,
                directory_id,
                directory.name,
                path,
                scan.trigger,
                options,
                scan.queued_at,
                scan.started_at or utcnow(),
            )
            min_mib = settings_store.load(db)["min_entry_mib"]
            reference = db.scalars(
                select(Scan)
                .where(Scan.directory_id == directory_id, Scan.status == "completed")
                .order_by(Scan.finished_at.desc(), Scan.id.desc())
            ).first()
            previous = (
                Previous(reference.dir_count, reference.file_count, reference.duration_seconds)
                if reference is not None
                else None
            )
        engine_id = str(options["engine"])
        engine = self.engines().get(engine_id)
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
        live.log = self._open_log(about, engine, min_mib)
        if scan_id in self._cancel_requests or self._stopping.is_set():
            self._fail(scan_id, "", "interrupted" if self._stopping.is_set() else "cancelled")
            return
        try:
            root = resolve(path, self.config.scan_root)
        except AppError as error:
            self._fail(scan_id, error.code)
            return
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
                keep = self._store_patiently(scan_id, directory_id, path, result_path, outcome)
                if live.log is not None:
                    entries = outcome.get("entries")
                    live.log.stored = entries if isinstance(entries, int) else None
                    # Its end now: pruning a large history can take minutes.
                    self._end_log(scan_id, live.log)
                if keep is not None:
                    try:
                        self.prune(directory_id, keep)
                    except Exception:
                        # The scan is stored; the next one prunes again.
                        log.exception("Old scans of directory %s could not be pruned", directory_id)
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
                # Any text the worker sends must be valid UTF-8, or the `done` line would be lost.
                message = clean(json.loads(raw.decode("utf-8", "replace")))
            except ValueError:
                continue
            if not isinstance(message, dict):
                continue
            kind = message.get("type")
            if kind == "progress":
                live.phase = str(message.get("phase", "scanning"))
                live.counters = bool(message.get("live", True))
                dirs = message.get("dirs", 0)
                live.dirs = int(dirs) if dirs is not None else None
                files = message.get("files")
                live.files = int(files) if files is not None else None
                live.done_bytes = int(message.get("bytes") or 0)
                live.current = message.get("current")
            elif kind == "done":
                done = message
            elif kind == "scan_error":
                if live.log is not None:
                    live.log.error(str(message.get("path", "")), str(message.get("message", "")))
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
        if live.log is not None:
            live.log.tail = list(tail)
        if not failure:
            details = " | ".join(line for line in tail[-3:] if line)
            failure = f"scan_failed: exit status {process.returncode}" + (
                f": {details}" if details else ""
            )
            if tail:
                log.warning("Scan worker %s failed:\n%s", live.scan_id, "\n".join(tail))
        return None, failure

    def _store_patiently(
        self, scan_id: int, directory_id: int, path: str, result_path: Path, done: dict[str, Any]
    ) -> int | None:
        """`_store`, tried again while the database stays busy: an hour of scanning is not lost
        to a moment of contention. Returns the scans to keep, or None when nothing was stored."""
        delay = RETRY_SECONDS
        for attempt in range(1, STORE_ATTEMPTS + 1):
            try:
                return self._store(scan_id, directory_id, path, result_path, done)
            except OperationalError:
                if attempt == STORE_ATTEMPTS or self._stopping.is_set():
                    raise
                log.warning("Scan %s could not be stored yet; trying again", scan_id, exc_info=True)
                if self._stopping.wait(delay):
                    raise
                delay *= 2
        return None

    def _store(
        self, scan_id: int, directory_id: int, path: str, result_path: Path, done: dict[str, Any]
    ) -> int | None:
        # Measured before the transaction: statvfs of a network share can be slow.
        volume = capacity.measure_folder(path, self.config.scan_root, directory_id)
        with self._storage_lock, transaction(self.sessions) as db:
            scan = db.get(Scan, scan_id)
            if scan is None or scan.status != "running":
                return None
            import_entries(db, scan_id, directory_id, result_path)
            scan.total_bytes = int(done["total_bytes"])
            file_count = done.get("file_count")
            scan.file_count = int(file_count) if file_count is not None else None
            scan.dir_count = int(done["dir_count"])
            scan.error_count = int(done.get("error_count") or 0)
            scan.error_sample = list(done.get("error_sample") or [])
            scan.has_detail = True
            if volume is not None:
                scan.capacity_total = volume.total
                scan.capacity_used = volume.used
                scan.capacity_free = volume.free
                capacity.store(db, directory_id, volume)
            self._close(scan, "completed", None)
            keep: int = settings_store.load(db)["keep_scans"]
            return keep


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


def elapsed_since(moment: datetime | None) -> float:
    return max((utcnow() - moment).total_seconds(), 0.0) if moment else 0.0
