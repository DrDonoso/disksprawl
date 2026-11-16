"""Pieces every scan engine shares: the job, exclusions, the folder tree, errors and progress.

A scan runs in a worker subprocess (`python -m disksprawl.engines JOB.json`). It writes progress as
JSON lines on stdout, the folders of the scan to the job's result file, and a final `done` line.
"""

import json
import os
import re
import sys
import threading
import time
from array import array
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from typing import IO, Any

ENGINES = ("native", "du", "ncdu", "gdu")
PACES = ("max", "balanced", "gentle")
MEASURES = ("apparent", "disk")
# Share of the time a paced scan rests: balanced rests a quarter of the time, gentle half.
PAUSE_SHARE = {"max": 0.0, "balanced": 0.25, "gentle": 0.5}
MAX_ERROR_SAMPLES = 20
PROGRESS_INTERVAL = 0.5


@dataclass
class Job:
    engine: str
    root: str
    result_path: str
    threads: int = 1
    pace: str = "max"
    measure: str = "apparent"
    one_filesystem: bool = True
    excludes: list[str] = field(default_factory=list)
    min_entry_bytes: int = 1024 * 1024
    binary: str | None = None

    @classmethod
    def load(cls, path: str) -> "Job":
        with open(path, encoding="utf-8") as handle:
            return cls(**json.load(handle))

    def dump(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(asdict(self), handle)


def display_name(name: str | bytes) -> str:
    """A file name as text that is always valid UTF-8, so it can be stored and sent as JSON.

    File names are bytes, and Linux does not require them to be UTF-8 (old Latin-1 names, for
    example). Bytes that are not UTF-8 become `\\xNN` escapes, whether the name comes as bytes or as
    the str Python makes of it (a surrogate for each such byte): `M\\xfasica`. Text that is already
    valid comes back unchanged.
    """
    if isinstance(name, str):
        if name.isascii():
            return name
        try:
            raw = name.encode("utf-8", "surrogateescape")
        except UnicodeEncodeError:
            # Surrogates that do not stand for a byte: escape their code units.
            raw = name.encode("utf-8", "surrogatepass")
    else:
        raw = name
    return raw.decode("utf-8", errors="backslashreplace")


def clean(value: Any) -> Any:
    """`value` (JSON-like) with every text passed through `display_name`."""
    if isinstance(value, str):
        return display_name(value)
    if isinstance(value, dict):
        return {display_name(str(key)): clean(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [clean(item) for item in value]
    return value


def unique_name(name: str, taken: set[str]) -> str:
    """`name`, or `name (2)`, `name (3)`… when a sibling already has it: names that differ only
    in bytes that are not UTF-8 can look the same (gdu writes every such byte as U+FFFD)."""
    if name not in taken:
        return name
    number = 2
    while f"{name} ({number})" in taken:
        number += 1
    return f"{name} ({number})"


def glob_to_regex(pattern: str, *, any_char: str = ".") -> str:
    """Translates a pattern of `*` and `?` to a regex fragment; everything else is literal."""
    parts = []
    for character in pattern:
        if character == "*":
            parts.append(f"{any_char}*")
        elif character == "?":
            parts.append(any_char)
        elif character in "\\.+()|[]{}^$#&~-":
            parts.append("\\" + character)
        else:
            parts.append(character)
    return "".join(parts)


class Excluder:
    """Matches file and folder names against the exclusion patterns (`*` and `?`)."""

    def __init__(self, patterns: list[str]):
        self.patterns = [pattern for pattern in patterns if pattern]
        self._regex = (
            re.compile("(?:" + "|".join(glob_to_regex(p) for p in self.patterns) + r")\Z", re.S)
            if self.patterns
            else None
        )

    def __call__(self, name: str) -> bool:
        return self._regex is not None and self._regex.match(name) is not None


class ErrorLog:
    """Counts the errors of a scan (permission denied, vanished files…) and keeps a sample."""

    def __init__(self) -> None:
        self.count = 0
        self.sample: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def add(self, path: str, message: str) -> None:
        with self._lock:
            self.count += 1
            if len(self.sample) < MAX_ERROR_SAMPLES:
                self.sample.append(
                    {"path": display_name(path), "message": display_name(message[:300])}
                )


def os_error_message(error: OSError) -> str:
    return error.strerror or type(error).__name__


class Tree:
    """Folders of a scan in flat arrays (millions of folders fit in a few hundred MiB).

    Each folder has a parent (-1 for the root, -2 while unknown), a name, and the bytes and files
    directly inside it. Totals are computed once, by `finalize`.
    """

    def __init__(self, *, count_files: bool = True):
        self.parent = array("q")
        self.names: list[str] = []
        self.own_bytes = array("q")
        self.own_files = array("q")
        self.count_files = count_files
        self.lock = threading.Lock()

    def __len__(self) -> int:
        return len(self.names)

    def add(self, parent: int, name: str, own_bytes: int = 0) -> int:
        name = display_name(name)
        with self.lock:
            self.parent.append(parent)
            self.names.append(name)
            self.own_bytes.append(own_bytes)
            self.own_files.append(0)
            return len(self.names) - 1

    def add_file(self, folder: int, size: int) -> None:
        with self.lock:
            self.own_bytes[folder] += size
            self.own_files[folder] += 1

    def add_bytes(self, folder: int, size: int) -> None:
        with self.lock:
            self.own_bytes[folder] += size

    def finalize(self, min_bytes: int) -> "Result":
        """Computes the totals and selects the folders to store (the root and every folder of at
        least `min_bytes`), parents before children."""
        count = len(self.names)
        roots = [index for index in range(count) if self.parent[index] == -1]
        if len(roots) != 1:
            raise RuntimeError(f"The scan produced {len(roots)} root folders")
        root = roots[0]
        # Children of each folder, in a compressed sparse row layout.
        offsets = array("q", bytes(8 * (count + 1)))
        for index in range(count):
            parent = self.parent[index]
            if parent >= 0:
                offsets[parent + 1] += 1
        for index in range(count):
            offsets[index + 1] += offsets[index]
        children = array("q", bytes(8 * max(count - 1, 0)))
        cursor = array("q", offsets[:count])
        for index in range(count):
            parent = self.parent[index]
            if parent >= 0:
                children[cursor[parent]] = index
                cursor[parent] += 1
        order = array("q")
        stack = [root]
        while stack:
            index = stack.pop()
            order.append(index)
            # Reversed, so the preorder keeps each folder's children in their original order.
            stack.extend(reversed(children[offsets[index] : offsets[index + 1]]))
        size = array("q", self.own_bytes)
        files = array("q", self.own_files)
        dirs = array("q", bytes(8 * count))
        for index in reversed(order):
            parent = self.parent[index]
            if parent >= 0:
                size[parent] += max(size[index], 0)
                files[parent] += files[index]
                dirs[parent] += dirs[index] + 1
        entries: list[tuple[int, int, str, int, int | None, int]] = []
        new_index = {root: 0}
        # Names already given to the stored children of each stored folder.
        sibling_names: dict[int, set[str]] = {}
        for index in order:
            if index != root and (size[index] < min_bytes or self.parent[index] not in new_index):
                continue
            new = len(entries)
            new_index[index] = new
            parent = self.parent[index]
            name = ""
            if index != root:
                taken = sibling_names.setdefault(new_index[parent], set())
                name = unique_name(self.names[index], taken)
                taken.add(name)
            entries.append((
                new,
                new_index[parent] if index != root else -1,
                name,
                max(size[index], 0),
                files[index] if self.count_files else None,
                dirs[index],
            ))
        return Result(
            entries=entries,
            total_bytes=max(size[root], 0),
            file_count=files[root] if self.count_files else None,
            dir_count=dirs[root],
        )


@dataclass
class Result:
    entries: list[tuple[int, int, str, int, int | None, int]]
    total_bytes: int
    file_count: int | None
    dir_count: int

    def write(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            for entry in self.entries:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")


def read_result(path: str) -> Iterator[tuple[int, int, str, int, int | None, int]]:
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            index, parent, name, size, files, dirs = json.loads(line)
            yield index, parent, name, size, files, dirs


class Progress:
    """Live counters of a scan, printed as JSON lines every half second by `start`."""

    def __init__(self, out: IO[str] | None = None, *, live: bool = True):
        self.out = out or sys.stdout
        self.live = live
        self.dirs = 0
        self.files: int | None = 0
        # True while the folders are counted with the files, in `files` (ncdu with several threads
        # draws a single counter): `dirs` is reported as null.
        self.dirs_in_files = False
        self.bytes = 0
        self.current: str | None = None
        self.phase = "scanning"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any]:
        current = self.current
        return {
            "type": "progress",
            "phase": self.phase,
            "live": self.live,
            "dirs": None if self.dirs_in_files else self.dirs,
            "files": self.files,
            "bytes": self.bytes,
            "current": display_name(current) if current is not None else None,
        }

    def emit(self, message: dict[str, Any]) -> None:
        # ASCII JSON of clean text: whatever the names or the locale, every line reaches the server.
        line = json.dumps(clean(message), ensure_ascii=True)
        with self._lock:
            self.out.write(line + "\n")
            self.out.flush()

    def start(self) -> None:
        def loop() -> None:
            while not self._stop.wait(PROGRESS_INTERVAL):
                self.emit(self.snapshot())

        self._thread = threading.Thread(target=loop, name="progress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join()
        self.emit(self.snapshot())


def relative(root: str, path: str) -> str:
    """`path` below `root` as `a/b/c` ('' for the root itself)."""
    if path == root:
        return ""
    prefix = root.rstrip("/") + "/"
    return path[len(prefix) :] if path.startswith(prefix) else path


def entry_size(stat: os.stat_result, measure: str) -> int:
    return stat.st_size if measure == "apparent" else stat.st_blocks * 512


def folder_size(stat: os.stat_result, measure: str) -> int:
    """A folder's own size: its blocks on disk; nothing in apparent size, like GNU du."""
    return 0 if measure == "apparent" else stat.st_blocks * 512


Clock = Callable[[], float]
Sleep = Callable[[float], None]
monotonic: Clock = time.monotonic
