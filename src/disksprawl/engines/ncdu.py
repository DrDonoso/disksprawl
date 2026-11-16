"""ncdu 2 in export mode: `ncdu -0 -o- [-x] [--exclude …] [-t N]`.

With one thread ncdu writes the export while it scans, so the scan has live counters. With more
threads it builds the export in memory and writes it at the end; its one-line progress (`-1`) on a
pseudo-terminal then gives the files and folders read so far, in one counter, and the folders being
read.
"""

import re
from dataclasses import dataclass, field
from typing import IO

from .common import ErrorLog, Excluder, Job, Progress, Tree, display_name, relative
from .export_json import Item, walk_export

ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
PROGRESS_LINE = re.compile(r"^\s*(\d+) files /\s*([\d.]+)\s*([KMGTPE]?i?B)\s*$")
THREAD_LINE = re.compile(r"^\s*#\d+: (.*)$")
UNITS = {"B": 1, "KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30, "TiB": 1 << 40, "PiB": 1 << 50,
         "EiB": 1 << 60}


def ncdu_command(job: Job, binary: str) -> list[str]:
    command = [binary, "--ignore-config", "-1" if job.threads > 1 else "-0", "-o-"]
    if job.one_filesystem:
        command.append("-x")
    for pattern in job.excludes:
        command += ["--exclude", pattern]
    if job.threads > 1:
        command += ["-t", str(job.threads)]
    return [*command, job.root]


@dataclass
class Folder:
    index: int
    path: str
    device: object
    skipped: bool = False


@dataclass
class ExportReader:
    """Builds the tree from an export with GNU du's rules: hard links count once, folders count
    their own blocks on disk (nothing in apparent size), and excluded items count nothing."""

    tree: Tree
    errors: ErrorLog
    progress: Progress
    excluded: Excluder
    size_key: str
    stack: list[Folder] = field(default_factory=list)
    seen_links: set[tuple[object, object]] = field(default_factory=set)
    finished: bool = False

    def folder(self, info: Item) -> None:
        name = str(info.get("name", ""))
        if not self.stack:
            path, parent_index, device = "", -1, info.get("dev")
        else:
            parent = self.stack[-1]
            if parent.skipped or "excluded" in info or self.excluded(name):
                self.stack.append(Folder(-1, "", None, skipped=True))
                return
            path = f"{parent.path}/{name}" if parent.path else name
            parent_index, device = parent.index, info.get("dev", parent.device)
        own = int(info.get("dsize") or 0) if self.size_key == "dsize" else 0
        index = self.tree.add(parent_index, display_name(name) if self.stack else "", own)
        if info.get("read_error"):
            self.errors.add(path, "read error")
        self.stack.append(Folder(index, path, device))
        self.progress.dirs += 1
        self.progress.bytes += own
        self.progress.current = path

    def file(self, item: Item) -> None:
        if not self.stack or self.stack[-1].skipped:
            return
        parent = self.stack[-1]
        name = str(item.get("name", ""))
        if "excluded" in item or self.excluded(name):
            return
        size = int(item.get(self.size_key) or 0)
        if item.get("hlnkc"):
            key = (item.get("dev", parent.device), item.get("ino"))
            if key in self.seen_links:
                size = 0
            else:
                self.seen_links.add(key)
        if item.get("read_error"):
            self.errors.add(f"{parent.path}/{name}" if parent.path else name, "read error")
        self.tree.add_file(parent.index, size)
        self.progress.bytes += size
        if self.progress.files is not None:
            self.progress.files += 1

    def folder_end(self) -> None:
        self.stack.pop()
        if not self.stack:
            self.finished = True


def parse_ncdu(
    stream: IO[bytes], job: Job, tree: Tree, errors: ErrorLog, progress: Progress
) -> bool:
    reader = ExportReader(
        tree, errors, progress, Excluder(job.excludes),
        "asize" if job.measure == "apparent" else "dsize",
    )
    walk_export(stream, reader.folder, reader.file, reader.folder_end)
    return reader.finished


class TerminalProgress:
    """Parses ncdu's one-line progress UI (`-1`), read from a pseudo-terminal: one counter of files
    and folders together (shown as the files), the bytes, and the folder each thread reads.

    When the walk ends, ncdu writes its export and draws that progress counting from 0 again: the
    walk's counters are kept, and the scan is finishing.
    """

    def __init__(self, root: str, progress: Progress):
        self.root = root
        self.progress = progress
        self.pending = ""
        # The folder of the first thread after each counter line is shown as the current one.
        self.first_thread = False
        self.walked = False

    def feed(self, text: str) -> None:
        self.pending += ANSI.sub("", text)
        *lines, self.pending = re.split(r"\r\n|\n|\r", self.pending)
        for line in lines:
            if self.walked:
                break
            match = PROGRESS_LINE.match(line)
            if match:
                items = int(match[1])
                if items < (self.progress.files or 0):
                    self.walked = True
                    self.progress.phase = "finishing"
                    break
                self.progress.files = items
                self.progress.bytes = int(float(match[2]) * UNITS.get(match[3], 1))
                self.first_thread = True
                continue
            thread = THREAD_LINE.match(line)
            if thread and self.first_thread:
                self.progress.current = relative(self.root, thread[1].strip())
                self.first_thread = False
