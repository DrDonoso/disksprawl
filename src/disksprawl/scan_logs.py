"""The log file of each scan: what was scanned and how, how it went, where its time went, how it
ended, and every path it could not read.

Each scan that starts gets a text file in DATA_DIR/logs/<directory name>/<YYYY-MM-DD_HHMM>.log,
named after the moment it started in the server's time zone (TZ), with `_2`, `_3`… when that name
is taken. Its path, relative to DATA_DIR/logs, is kept in scans.log_file: renaming a directory
leaves its earlier logs where they are. A scan cancelled while it was queued never started, and has
no log.

A log is written while its scan runs: a header with the directory and the options, then one line
per event with its time: queued, started, its progress every PROGRESS_SECONDS with the folder being
read, each path that could not be read, each folder that took long to read (Native), the phases
that follow the reading, and the end, with where the time went (engines/timing.py). Writing is
best effort: when it fails (a full disk), the app's log says so once and the scan carries on.

Logs expire `scan_log_days` days (⚙ Settings → General) after their last line, usually the end of
their scan: the scheduler sweeps them every hour. Deleting a directory deletes its logs.

Names come from the user and from the file system: every name and path goes through
`display_name` and loses its control characters, so each event stays on one line, and the folder of
a directory's logs keeps only characters that are safe in a file name, so no log lands anywhere
but in its own folder below DATA_DIR/logs.
"""

import logging
import math
import os
import posixpath
import re
import threading
import time
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Any, TypeGuard
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .capacity import percent
from .db import Scan, utcnow
from .engines import EngineInfo
from .engines.common import MAX_REPORTED_ERRORS, display_name
from .engines.timing import MAX_REPORTED_SLOW_FOLDERS, MIN_SHARE

log = logging.getLogger(__name__)

SUFFIX = ".log"
DAY_SECONDS = 86_400
# Seconds between two progress lines while a scan reads the folders.
PROGRESS_SECONDS = 10.0
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


def increase(count: int, one: str, other: str) -> str:
    """`+1 folder`, `+2,001 files`."""
    return f"{count:+,} {one if count == 1 else other}"


def seconds_text(seconds: float) -> str:
    """`0.4 s`, `12.3 s`, then as duration_text: `4 min 05 s`."""
    return f"{seconds:.1f} s" if seconds < 60 else duration_text(seconds)


def amount(value: Any) -> float | None:
    """A number of seconds from the worker, or None when it is not one (an integer too large for a
    float among them)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) and number >= 0 else None


def is_count(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def where(root: str, path: str) -> str:
    """A path the worker reports, relative to the scanned folder, below the folder as configured."""
    return one_line(posixpath.join(root, path) if path else root)


def table(rows: list[list[str]]) -> list[str]:
    """Aligned, indented rows: the first cell to the left, the middle ones to the right, the last
    one (a path) as it is."""
    if not rows:
        return []
    widths = [max(len(row[column]) for row in rows) for column in range(len(rows[0]) - 1)]
    lines = []
    for row in rows:
        cells = [row[0].ljust(widths[0])]
        cells += [cell.rjust(width) for cell, width in zip(row[1:-1], widths[1:], strict=True)]
        lines.append("    " + "  ".join([*cells, row[-1]]))
    return lines


def timing_lines(timing: Any, save_seconds: float | None, root: str, pace: str | None) -> list[str]:
    """Where the time of a completed scan went, from the worker's summary (engines/timing.py) and
    the saving: the phases, then with the Native engine the slowest folders and the reading time by
    folder. Whatever the summary lacks is left out."""
    summary: dict[str, Any] = timing if isinstance(timing, dict) else {}
    phases = []
    scan = amount(summary.get("scan_seconds"))
    if scan is not None:
        phases.append(f"Reading the folders: {seconds_text(scan)}")
        rest = amount(summary.get("rest_seconds"))
        if rest and scan:
            phases.append(
                f"  Resting ({PACES.get(pace or '', 'pace')}): {seconds_text(rest)},"
                f" {round(100 * min(rest / scan, 1))} % of it"
            )
    finish = amount(summary.get("finish_seconds"))
    if finish is not None:
        phases.append(f"Adding up the results: {seconds_text(finish)}")
    if save_seconds is not None:
        phases.append(f"Saving the results: {seconds_text(save_seconds)}")
    lines = ["  Time:", *(f"    {line}" for line in phases)] if phases else []
    return lines + slowest_lines(summary.get("slowest"), root) + folder_lines(summary, root)


def slowest_lines(items: Any, root: str) -> list[str]:
    rows = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        seconds = amount(item.get("seconds"))
        if seconds is None:
            continue
        entries = item.get("entries")
        count = plural(entries, "entry", "entries") if is_count(entries) else ""
        rows.append([seconds_text(seconds), count, where(root, item["path"])])
    if not rows:
        return []
    return ["  Slowest folders to read, without their subfolders:", *table(rows)]


def folder_lines(summary: dict[str, Any], root: str) -> list[str]:
    reading = amount(summary.get("reading_seconds"))
    items = summary.get("folders")
    if not reading or not isinstance(items, list):
        return []

    def row(item: Any, indent: str) -> list[str] | None:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            return None
        seconds = amount(item.get("seconds"))
        if seconds is None:
            return None
        folders = item.get("folders")
        return [
            indent + seconds_text(seconds),
            f"{100 * seconds / reading:.1f} %",
            plural(folders, "folder", "folders") if is_count(folders) else "",
            where(root, item["path"]),
        ]

    rows = []
    for item in items:
        line = row(item, "")
        if line is None:
            continue
        rows.append(line)
        subfolders = item.get("subfolders")
        for subfolder in subfolders if isinstance(subfolders, list) else []:
            nested = row(subfolder, "  ")
            if nested is not None:
                rows.append(nested)
    own = amount(summary.get("own_seconds"))
    if rows and own and own >= MIN_SHARE * reading:
        rows.append([
            seconds_text(own),
            f"{100 * own / reading:.1f} %",
            "",
            f"{where(root, '')}, without its subfolders",
        ])
    if not rows:
        return []
    title = "  Reading time by folder, with its subfolders"
    threads = summary.get("threads")
    if is_count(threads) and threads > 1:
        title += f", added up over the {threads} threads"
    return [f"{title}:", *table(rows)]


@dataclass(frozen=True)
class Sample:
    """The counters of the last progress line, and when it was written."""

    time: float
    dirs: int | None
    files: int | None
    bytes: int


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
        pace: str | None = None,
    ):
        self.scan_id = scan_id
        self.zone = zone
        self.relative = relative
        # The scanned folder as configured: errors are reported relative to it.
        self.root = root
        self.pace = pace
        # Read errors listed so far, folders stored, and the last lines of a failed worker.
        self.listed = 0
        self.stored: int | None = None
        self.tail: list[str] = []
        # The phase of the scan, the last progress line (the start, at first), whether the engine
        # reports no progress, and the slow folders listed so far.
        self.phase = "scanning"
        self.sample = Sample(time.monotonic(), 0, 0, 0)
        self.silent = False
        self.slow = 0
        # Where the time went: the worker's summary, and the seconds saving took.
        self.timing: Any = None
        self.save_seconds: float | None = None
        self.ended = False
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
        self.event(f"Error: {where(self.root, path)}: {one_line(message)}")

    def progress(
        self,
        phase: str,
        counters: bool,
        dirs: int | None,
        files: int | None,
        done_bytes: int,
        current: str | None,
        share: float | None = None,
    ) -> None:
        """The progress the worker sends every half second: a line every PROGRESS_SECONDS while it
        reads the folders, and one when it starts adding up the results. `dirs` is None when the
        folders are counted with the files, `files` when they are not counted; `share` is the
        estimated share done."""
        if phase != self.phase:
            self.phase = phase
            if phase == "finishing":
                self.event("Adding up the results")
            return
        if phase != "scanning":
            return
        if not counters:
            if not self.silent:
                self.silent = True
                self.event("The engine reports no progress until it ends")
            return
        now = time.monotonic()
        last = self.sample
        if now - last.time < PROGRESS_SECONDS:
            return
        totals = []
        changes = []
        if dirs is None:
            items = files or 0
            totals.append(plural(items, "file or folder", "files and folders"))
            changes.append(increase(items - (last.files or 0), "file or folder", "files and folders"))
        else:
            totals.append(plural(dirs, "folder", "folders"))
            changes.append(increase(dirs - (last.dirs or 0), "folder", "folders"))
            if files is not None:
                totals.append(plural(files, "file", "files"))
                changes.append(increase(files - (last.files or 0), "file", "files"))
        totals.append(f"{size_text(done_bytes)} read")
        grown = done_bytes - last.bytes
        changes.append(("+" if grown >= 0 else "-") + size_text(abs(grown)))
        text = f"Progress: {', '.join(totals)}"
        if share is not None:
            text += f", about {round(100 * share)} % done"
        text += f"; in the last {round(now - last.time)} s: {', '.join(changes)}"
        if current is not None:
            text += f"; reading {where(self.root, current)}"
        self.event(text)
        self.sample = Sample(now, dirs, files, done_bytes)

    def slow_folder(self, path: str, seconds: Any, entries: Any) -> None:
        """A folder that took long to read, without its subfolders (Native)."""
        took = amount(seconds)
        if took is None or self.slow >= MAX_REPORTED_SLOW_FOLDERS:
            return
        self.slow += 1
        text = f"Slow folder: {where(self.root, path)} took {seconds_text(took)}"
        if is_count(entries):
            text += f" for {plural(entries, 'entry', 'entries')}"
        self.event(text)

    def saving(self) -> None:
        self.event(
            "Saving the results"
            + (f": {plural(self.stored, 'folder', 'folders')}" if self.stored is not None else "")
        )

    def pruned(self, scans: int, keep: int, seconds: float) -> None:
        """Older scans of the directory lost their folders after this one completed."""
        self.event(
            f"Removed the folders of {plural(scans, 'older scan', 'older scans')} in"
            f" {seconds_text(seconds)}, keeping those of the latest {plural(keep, 'scan', 'scans')}"
        )

    def end(self, scan: Scan) -> None:
        """How the scan ended, from its record, and where its time went."""
        self.ended = True
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
            self.write(
                *(f"  {line}" for line in details),
                *timing_lines(self.timing, self.save_seconds, self.root, self.pace),
            )
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
                        str(about.options.get("pace", "")),
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
