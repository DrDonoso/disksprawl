"""GNU du: `du -0 [-b | -B1] [-x] [--exclude=…]` lists every folder after its contents, with its
total size, separated by NUL bytes. It reads with one thread and does not count files."""

import re
from typing import IO

from .common import ErrorLog, Job, Progress, Tree, display_name

# du quotes file names in its messages like a shell would: 'a b', "it's", 'ni'$'\361''os'…
QUOTED = r"""(?:'[^']*'|"(?:[^"\\]|\\.)*"|\$'(?:[^'\\]|\\.)*'|\\.)+"""
# …and starts them with its name as it was run: `du: `, `/usr/bin/du: `, `/usr/bin/gnudu: `.
PROGRAM = r"[^\s:'\"]+: "
ERROR = re.compile(rf"^{PROGRAM}(?P<what>.*?) ?(?P<path>{QUOTED}): (?P<reason>[^'\"]*)$")
MESSAGE = re.compile(rf"^{PROGRAM}(?P<message>.+)$")
C_ESCAPES = {"a": 7, "b": 8, "e": 27, "E": 27, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11}


def shell_unquote(word: str) -> bytes:
    """The bytes of a word quoted as GNU coreutils quotes file names in its messages."""
    out = bytearray()
    position, end = 0, len(word)

    def literal(text: str) -> bytes:
        return text.encode("utf-8", "surrogateescape")

    while position < end:
        character = word[position]
        if character == "'":
            close = word.find("'", position + 1)
            close = end if close < 0 else close
            out += literal(word[position + 1 : close])
            position = close + 1
        elif character == '"':
            position += 1
            while position < end and word[position] != '"':
                escaped = word[position + 1 : position + 2]
                if word[position] == "\\" and escaped in ("\\", '"', "$", "`"):
                    position += 1
                out += literal(word[position])
                position += 1
            position += 1
        elif word.startswith("$'", position):
            position += 2
            while position < end and word[position] != "'":
                if word[position] != "\\" or position + 1 >= end:
                    out += literal(word[position])
                    position += 1
                    continue
                escape = word[position + 1]
                octal = re.match(r"[0-7]{1,3}", word[position + 1 : position + 4])
                hexadecimal = re.match(r"x([0-9a-fA-F]{1,2})", word[position + 1 : position + 4])
                if octal:
                    out.append(int(octal[0], 8) & 0xFF)
                    position += 1 + len(octal[0])
                elif hexadecimal:
                    out.append(int(hexadecimal[1], 16))
                    position += 1 + len(hexadecimal[0])
                else:
                    out += bytes([C_ESCAPES[escape]]) if escape in C_ESCAPES else literal(escape)
                    position += 2
            position += 1
        elif character == "\\" and position + 1 < end:
            out += literal(word[position + 1])
            position += 2
        else:
            out += literal(character)
            position += 1
    return bytes(out)


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
        what = match["what"].strip().rstrip(":")
        reason = match["reason"].strip()
        path = shell_unquote(match["path"])
        prefix = root.encode("utf-8", "surrogateescape").rstrip(b"/")
        if path == prefix:
            path = b""
        elif path.startswith(prefix + b"/"):
            path = path[len(prefix) + 1 :]
        errors.add(display_name(path), f"{what}: {reason}" if what else reason)
    elif other := MESSAGE.match(line):
        errors.add("", other["message"].strip())
