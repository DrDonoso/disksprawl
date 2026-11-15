"""GNU du: `du -0 [-b | -B1] [-x] [--exclude=…]` lists every folder after its contents, with its
total size, separated by NUL bytes. It reads with one thread and does not count files."""

import re
from typing import IO

from .common import ErrorLog, Job, Progress, Tree, display_name, relative

ERROR = re.compile(r"^du: (?P<what>.*?)'(?P<path>.*)': (?P<reason>.*)$")


def du_command(job: Job, binary: str) -> list[str]:
    command = [binary, "-0", "-b" if job.measure == "apparent" else "-B1"]
    if job.one_filesystem:
        command.append("-x")
    command += [f"--exclude={pattern}" for pattern in job.excludes]
    return [*command, "--", job.root]


def parse_du(stream: IO[bytes], root: str, tree: Tree, progress: Progress) -> bool:
    """Builds `tree` from du's output; True once the root folder (always last) has been read."""
    root_bytes = root.encode("utf-8", "surrogateescape")
    # Folders read whose parent is still to come (du lists contents first), by parent path.
    pending: dict[bytes, list[int]] = {}
    pending_bytes: dict[bytes, int] = {}
    finished = False
    buffer = b""

    def handle(record: bytes) -> None:
        nonlocal finished
        size_text, _, path = record.partition(b"\t")
        size = int(size_text)
        if path == root_bytes:
            relative_path = b""
        elif path.startswith(root_bytes + b"/"):
            relative_path = path[len(root_bytes) + 1 :]
        else:
            return
        name = relative_path.rsplit(b"/", 1)[-1]
        parent_path = relative_path.rsplit(b"/", 1)[0] if b"/" in relative_path else b""
        own = size - pending_bytes.pop(relative_path, 0)
        index = tree.add(-1 if not relative_path else -2, display_name(name), max(own, 0))
        for child in pending.pop(relative_path, []):
            tree.parent[child] = index
        if relative_path:
            pending.setdefault(parent_path, []).append(index)
            pending_bytes[parent_path] = pending_bytes.get(parent_path, 0) + size
        else:
            finished = True
        progress.dirs += 1
        progress.bytes += max(own, 0)

    while chunk := stream.read(65536):
        buffer += chunk
        *records, buffer = buffer.split(b"\0")
        for record in records:
            if record:
                handle(record)
    if buffer:
        handle(buffer)
    return finished


def du_error(line: str, root: str, errors: ErrorLog) -> None:
    """Records one line of du's stderr (`du: cannot read directory '…': Permission denied`)."""
    match = ERROR.match(line)
    if match:
        what = match["what"].strip()
        reason = match["reason"].strip()
        errors.add(relative(root, match["path"]), f"{what}: {reason}" if what else reason)
    elif line.startswith("du: "):
        errors.add("", line[4:].strip())
