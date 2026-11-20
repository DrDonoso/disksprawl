"""The log file of each scan: what was scanned and how, how it ended, and every path it could not
read.

Each scan that starts gets a text file in DATA_DIR/logs/<directory name>/<YYYY-MM-DD_HHMM>.log,
named after the moment it started in the server's time zone (TZ), with `_2`, `_3`… when that name
is taken. Its path, relative to DATA_DIR/logs, is kept in scans.log_file: renaming a directory
leaves its earlier logs where they are. A scan cancelled while it was queued never started, and has
no log.

A log is written while its scan runs: a header with the directory and the options, then one line
per event with its time (queued, started, each path that could not be read, the end). Writing is
best effort: when it fails (a full disk), the app's log says so once and the scan carries on.

Logs expire `scan_log_days` days (⚙ Settings → General) after their last line, usually the end of
their scan: the scheduler sweeps them every hour. Deleting a directory deletes its logs.

Names come from the user and from the file system: every name and path goes through
`display_name` and loses its control characters, so each event stays on one line, and the folder of
a directory's logs keeps only characters that are safe in a file name, so no log lands anywhere
but in its own folder below DATA_DIR/logs.
"""

import logging
import os
import posixpath
import re
import threading
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .capacity import percent
from .db import Scan, utcnow
from .engines import EngineInfo
from .engines.common import MAX_REPORTED_ERRORS, display_name

log = logging.getLogger(__name__)

SUFFIX = ".log"
DAY_SECONDS = 86_400
# Characters a folder name never keeps: path separators, those Windows does not allow in file names
# (the logs may be copied there) and control characters.
UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]')
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
MAX_FOLDER_LENGTH = 80
# Logs of one directory name that may start within the same minute (`_2`, `_3`…).
MAX_SAME_MINUTE = 1000
UNITS = ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB")
PACES = {"max": "maximum pace", "balanced": "balanced pace", "gentle": "gentle pace"}
MEASURES = {"apparent": "apparent size", "disk": "space on disk"}
# Why a scan failed, by the code its message starts with (as the interface and the notifications
# word them).
REASONS = {
    "path_invalid": "the folder's path is not valid",
    "path_not_found": "the folder does not exist (it may not be mounted)",
    "path_unreadable": "the folder cannot be read: check its permissions",
    "path_outside_root": "the folder must be inside /data",
    "path_not_directory": "the path is a file, not a folder",
    "path_unavailable": "the folder is not available",
    "engine_unavailable": "its scan engine is not installed",
    "engine_failed": "the scan engine failed",
    "scan_failed": "the scan failed",
    "internal_error": "unexpected server error: the container's log tells more",
}


def one_line(text: str) -> str:
    """`text` as valid UTF-8 on a single line: control characters become `\\xNN` escapes, as the
    bytes of names that are not UTF-8 do."""
    return CONTROL.sub(lambda match: f"\\x{ord(match[0]):02x}", display_name(text))


def folder_name(name: str, directory_id: int) -> str:
    """The folder of a directory's logs: its name, keeping only characters safe in a file name."""
    folder = UNSAFE.sub("_", display_name(name)).strip(" .")[:MAX_FOLDER_LENGTH].rstrip(" .")
    return folder or f"directory-{directory_id}"


def download_name(relative: str) -> str:
    """The name a log downloads as: `Media/2026-11-20_0300.log` → `Media_2026-11-20_0300.log`."""
    return relative.replace("/", "_")


def content_disposition(name: str) -> str:
    """An attachment named `name`, with an ASCII fallback for older clients (RFC 6266)."""
    fallback = "".join(character if " " <= character < "\x7f" else "_" for character in name)
    if fallback == name:
        return f'attachment; filename="{name}"'
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(name, safe='')}"


def timestamp(moment: datetime, zone: ZoneInfo) -> str:
    return moment.astimezone(zone).isoformat(sep=" ", timespec="seconds")


def size_text(size: int) -> str:
    """Binary units with three significant digits, as the interface writes them (`1.23 TiB`)."""
    value = float(max(size, 0))
    unit = 0
    while value >= 1024 and unit < len(UNITS) - 1:
        value /= 1024
        unit += 1
    digits = 0 if unit == 0 or value >= 100 else 1 if value >= 10 else 2
    return f"{value:.{digits}f} {UNITS[unit]}"


def duration_text(seconds: float | None) -> str:
    """`12 s`, `4 min 05 s`, `2 h 03 min 41 s`."""
    minutes, secs = divmod(max(round(seconds or 0), 0), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} min {secs:02d} s"
    if minutes:
        return f"{minutes} min {secs:02d} s"
    return f"{secs} s"


def plural(count: int, one: str, other: str) -> str:
    return f"{count:,} {one if count == 1 else other}"


@dataclass(frozen=True)
class About:
    """What a log is about: its scan and the directory, as they were when the scan started."""

    scan_id: int
    directory_id: int
    name: str
    path: str
    trigger: str
    options: dict[str, Any]
    queued_at: datetime
    started_at: datetime


def header(about: About, engine: EngineInfo | None, min_mib: int, version: str) -> list[str]:
    options = about.options
    engine_id = str(options.get("engine", ""))
    if engine is None or not engine.available:
        engine_text = f"{engine_id} (not installed)"
        threads = int(options.get("threads", 1))
    else:
        engine_text = (
            f"{engine_id} ({engine.version})"
            if engine.binary is None
            else f"{engine_id} {engine.version} ({engine.binary})"
        )
        threads = max(1, min(int(options.get("threads", 1)), engine.max_threads))
    settings = [
        plural(threads, "thread", "threads"),
        PACES.get(str(options.get("pace")), str(options.get("pace"))),
        "low priority" if options.get("low_priority") else "normal priority",
        MEASURES.get(str(options.get("measure")), str(options.get("measure"))),
        "one file system" if options.get("one_filesystem") else "every file system",
    ]
    excludes = ", ".join(one_line(str(pattern)) for pattern in options.get("excludes") or [])
    return [
        f"DiskSprawl {one_line(version)}: scan {about.scan_id}",
        f"Directory: {one_line(about.name)} (id {about.directory_id})",
        f"Folder: {one_line(about.path)}",
        f"Started by: {'its schedule' if about.trigger == 'scheduled' else 'Scan now'}",
        f"Engine: {one_line(engine_text)}",
        f"Options: {', '.join(settings)}",
        f"Excluded: {excludes or 'nothing'}",
        f"Smallest folder stored: {min_mib} MiB",
        "",
    ]


class ScanLog:
    """The log of one scan, open for appending. Every write is best effort: after a failure the
    log stops, and the scan carries on."""

    def __init__(
        self,
        scan_id: int,
        zone: ZoneInfo,
        handle: IO[str] | None = None,
        relative: str | None = None,
        root: str = "",
    ):
        self.scan_id = scan_id
        self.zone = zone
        self.relative = relative
        # The scanned folder as configured: errors are reported relative to it.
        self.root = root
        # Read errors listed so far, folders stored, and the last lines of a failed worker.
        self.listed = 0
        self.stored: int | None = None
        self.tail: list[str] = []
        self._handle = handle

    @property
    def open(self) -> bool:
        return self._handle is not None

    def write(self, *lines: str) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.write("".join(line + "\n" for line in lines))
            handle.flush()
        except (OSError, ValueError) as error:
            log.warning(
                "The log of scan %s could not be written (%s); it stops here",
                self.scan_id,
                type(error).__name__,
            )
            self.close()

    def event(self, text: str, moment: datetime | None = None) -> None:
        self.write(f"{timestamp(moment or utcnow(), self.zone)}  {text}")

    def error(self, path: str, message: str) -> None:
        """A path the scan could not read (relative to the scanned folder)."""
        if self.listed >= MAX_REPORTED_ERRORS:
            return
        self.listed += 1
        where = posixpath.join(self.root, path) if path else self.root
        self.event(f"Error: {one_line(where)}: {one_line(message)}")

    def end(self, scan: Scan) -> None:
        """How the scan ended, from its record."""
        moment = scan.finished_at or utcnow()
        took = duration_text(scan.duration_seconds)
        if scan.status == "completed":
            total = scan.total_bytes or 0
            self.event(f"Completed in {took}", moment)
            details = [f"Size: {size_text(total)} ({total:,} bytes)"]
            if scan.file_count is not None:
                details.append(f"Files: {scan.file_count:,}")
            details.append(f"Folders: {scan.dir_count or 0:,}")
            if self.stored is not None:
                details.append(f"Folders stored: {self.stored:,}")
            details.append(self._errors(scan.error_count))
            if scan.capacity_used is not None and scan.capacity_free is not None:
                details.append(
                    f"Volume: {percent(scan.capacity_used, scan.capacity_free):.1f} % used"
                    f" ({size_text(scan.capacity_used)} used, {size_text(scan.capacity_free)}"
                    f" free, {size_text(scan.capacity_total or 0)} in total)"
                )
            self.write(*(f"  {line}" for line in details))
        elif scan.status == "failed":
            reason = REASONS.get((scan.message or "").split(":", 1)[0])
            self.event(f"Failed after {took}" + (f": {reason}" if reason else ""), moment)
            if scan.message:
                self.write(f"  Reason: {one_line(scan.message)}")
            if self.tail:
                self.write(
                    "  Last lines of the scan worker:", *(f"    {one_line(line)}" for line in self.tail)
                )
        elif scan.status == "cancelled":
            self.event(f"Cancelled after {took}", moment)
        elif scan.status == "interrupted":
            self.event("Interrupted: DiskSprawl stopped during the scan", moment)

    def _errors(self, count: int) -> str:
        if not count:
            return "Errors: none"
        if self.listed >= count:
            return f"Errors: {count:,} (listed above)"
        if self.listed:
            return f"Errors: {count:,} (the first {self.listed:,} are listed above)"
        return f"Errors: {count:,}"

    def close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


class ScanLogs:
    """The folder of the scans' logs, DATA_DIR/logs."""

    def __init__(self, root: Path, zone: ZoneInfo):
        self.root = root
        self.zone = zone
        # Creating a log and removing a folder that became empty never overlap.
        self._folders = threading.Lock()

    def path(self, relative: str) -> Path | None:
        """The file of a log; None when `relative` does not name one in a folder of the logs."""
        parts = relative.split("/")
        if (
            len(parts) != 2
            or any(part in ("", ".", "..") or "\\" in part or "\0" in part for part in parts)
            or not parts[1].endswith(SUFFIX)
        ):
            return None
        return self.root / parts[0] / parts[1]

    def create(self, about: About) -> ScanLog:
        """A new, empty log for the scan; a closed one when it cannot be created."""
        folder_text = folder_name(about.name, about.directory_id)
        stem = about.started_at.astimezone(self.zone).strftime("%Y-%m-%d_%H%M")
        folder = self.root / folder_text
        try:
            with self._folders:
                self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
                folder.mkdir(mode=0o700, exist_ok=True)
                for number in range(1, MAX_SAME_MINUTE + 1):
                    name = f"{stem}{SUFFIX}" if number == 1 else f"{stem}_{number}{SUFFIX}"
                    try:
                        descriptor = os.open(
                            folder / name, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_EXCL, 0o600
                        )
                    except FileExistsError:
                        continue
                    return ScanLog(
                        about.scan_id,
                        self.zone,
                        self._text(descriptor),
                        f"{folder_text}/{name}",
                        about.path,
                    )
        except OSError as error:
            log.warning(
                "The log of scan %s could not be created (%s)", about.scan_id, type(error).__name__
            )
        return ScanLog(about.scan_id, self.zone)

    def reopen(self, scan_id: int, relative: str) -> ScanLog:
        """The existing log of a scan, to add its end; a deleted log is never created again."""
        path = self.path(relative)
        if path is not None:
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_APPEND)
                return ScanLog(scan_id, self.zone, self._text(descriptor), relative)
            except OSError:
                pass
        return ScanLog(scan_id, self.zone)

    def read(self, relative: str) -> bytes | None:
        path = self.path(relative)
        if path is None:
            return None
        try:
            return path.read_bytes()
        except OSError:
            return None

    def delete(self, relatives: Iterable[str]) -> None:
        folders: set[Path] = set()
        for relative in relatives:
            path = self.path(relative)
            if path is None:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                log.warning("A scan log could not be deleted (%s)", type(error).__name__)
            folders.add(path.parent)
        for folder in folders:
            self._remove_if_empty(folder)

    def expired(self, days: int, keep: Collection[str], now: float) -> list[str]:
        """The logs whose last line is more than `days` days old, except those in `keep` (the
        scans still running)."""
        cutoff = now - days * DAY_SECONDS
        found: list[str] = []
        for folder in self._folder_paths():
            try:
                with os.scandir(folder) as entries:
                    files = [
                        entry
                        for entry in entries
                        if entry.name.endswith(SUFFIX) and entry.is_file(follow_symlinks=False)
                    ]
            except OSError:
                continue
            for entry in files:
                relative = f"{os.path.basename(folder)}/{entry.name}"
                if relative in keep:
                    continue
                try:
                    if entry.stat(follow_symlinks=False).st_mtime < cutoff:
                        found.append(relative)
                except OSError:
                    continue
        return found

    def remove_empty_folders(self) -> None:
        for folder in self._folder_paths():
            self._remove_if_empty(Path(folder))

    def _folder_paths(self) -> list[str]:
        try:
            with os.scandir(self.root) as entries:
                return [entry.path for entry in entries if entry.is_dir(follow_symlinks=False)]
        except FileNotFoundError:
            return []

    def _remove_if_empty(self, folder: Path) -> None:
        with self._folders:
            try:
                folder.rmdir()
            except OSError:
                pass

    @staticmethod
    def _text(descriptor: int) -> IO[str]:
        return os.fdopen(descriptor, "w", encoding="utf-8", errors="backslashreplace", newline="\n")
