"""gdu in export mode: `gdu -n -p -o- [-x] [-X patterns] [--sequential | -m N]`.

gdu writes its export (ncdu's JSON format) only when the scan ends, so there are no live counters.
Its folders carry cumulative sizes; on disk they include the folder's own blocks, which are worked
out from them (an apparent size counts the files only, as in GNU du).
"""

import re
from dataclasses import dataclass, field
from typing import IO

from .common import (
    ErrorLog,
    Excluder,
    Job,
    Progress,
    Tree,
    display_name,
    glob_to_regex,
    relative,
)
from .export_json import Item, walk_export

MAX_THREADS = 8
LOG_ERROR = re.compile(r'msg="(?:open|lstat|stat|readdirent|readdir|read) (?P<path>.+): (?P<reason>[^"]+)"')


def ignore_patterns(job: Job) -> list[str]:
    """gdu ignores folders whose full path matches a regex: one per exclusion pattern."""
    return [
        ".*/" + glob_to_regex(pattern, any_char="[^/]").replace(" ", r"\x20")
        for pattern in job.excludes
    ]


def gdu_command(job: Job, binary: str, ignore_file: str | None, log_file: str) -> list[str]:
    # The log holds the reason of each read error; the export only flags the folder.
    command = [binary, "-n", "-p", "-c", "-o-", "-l", log_file]
    if job.one_filesystem:
        command.append("-x")
    if ignore_file:
        command += ["-X", ignore_file]
    if job.threads <= 1:
        command.append("--sequential")
    else:
        command += ["-m", str(min(job.threads, MAX_THREADS))]
    return [*command, job.root]


@dataclass
class Folder:
    index: int
    path: str
    reported: int
    children: int = 0
    skipped: bool = False


@dataclass
class GduReader:
    root: str
    tree: Tree
    errors: ErrorLog
    progress: Progress
    excluded: Excluder
    apparent: bool
    stack: list[Folder] = field(default_factory=list)
    seen_links: set[object] = field(default_factory=set)
    read_errors: list[str] = field(default_factory=list)
    finished: bool = False

    def folder(self, info: Item) -> None:
        name = str(info.get("name", ""))
        reported = int(info.get("dsize") or 0)
        if not self.stack:
            path, parent_index = "", -1
        else:
            parent = self.stack[-1]
            if parent.skipped or self.excluded(name):
                parent.children += reported
                self.stack.append(Folder(-1, "", reported, skipped=True))
                return
            path = f"{parent.path}/{name}" if parent.path else name
            parent_index = parent.index
        index = self.tree.add(parent_index, display_name(name) if self.stack else "")
        if info.get("read_error"):
            self.read_errors.append(path)
        self.stack.append(Folder(index, path, reported))
        self.progress.dirs += 1
        self.progress.current = path

    def file(self, item: Item) -> None:
        if not self.stack or self.stack[-1].skipped:
            return
        parent = self.stack[-1]
        size = int(item.get("asize" if self.apparent else "dsize") or 0)
        disk = int(item.get("dsize") or 0)
        if item.get("hlnkc"):
            inode = item.get("ino")
            if inode in self.seen_links:
                size = disk = 0
            else:
                self.seen_links.add(inode)
        parent.children += disk
        if self.excluded(str(item.get("name", ""))):
            return
        self.tree.add_file(parent.index, size)
        self.progress.bytes += size
        if self.progress.files is not None:
            self.progress.files += 1

    def folder_end(self) -> None:
        folder = self.stack.pop()
        if folder.skipped:
            return
        if not self.apparent:
            # What gdu's total holds besides the contents: the folder's own blocks.
            own = max(folder.reported - folder.children, 0)
            self.tree.add_bytes(folder.index, own)
            self.progress.bytes += own
        if self.stack:
            self.stack[-1].children += folder.reported
        else:
            self.finished = True


def parse_gdu(
    stream: IO[bytes], job: Job, tree: Tree, errors: ErrorLog, progress: Progress
) -> GduReader:
    reader = GduReader(
        job.root, tree, errors, progress, Excluder(job.excludes), job.measure == "apparent"
    )
    walk_export(stream, reader.folder, reader.file, reader.folder_end)
    return reader


def gdu_errors(reader: GduReader, log_lines: list[str], errors: ErrorLog) -> None:
    """Folders gdu could not read, with the reason it logged when there is one."""
    reasons: dict[str, str] = {}
    for line in log_lines:
        match = LOG_ERROR.search(line)
        if match:
            reasons[relative(reader.root, match["path"])] = match["reason"]
    for path in reader.read_errors:
        errors.add(path, reasons.get(path, "read error"))
