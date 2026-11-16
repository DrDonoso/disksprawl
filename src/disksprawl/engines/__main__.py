"""Scan worker: `python -m disksprawl.engines JOB.json`.

Runs one scan with the job's engine, prints JSON lines on stdout (`progress` every half second, then
`done` or `error`) and writes the folders of the scan to the job's result file. The server starts
it at low priority when the directory asks for it (nice, ionice) and stops it with SIGTERM.
"""

import fcntl
import os
import pty
import signal
import struct
import subprocess
import sys
import termios
import threading
from collections.abc import Callable
from typing import IO, NoReturn

from .common import ErrorLog, Job, Progress, Tree
from .du import du_command, du_error, parse_du
from .gdu import GduReader, gdu_command, gdu_errors, ignore_patterns, parse_gdu
from .native import scan_native
from .ncdu import TerminalProgress, ncdu_command, parse_ncdu
from .pacing import Pacer

TAIL_LINES = 40
child: subprocess.Popen[bytes] | None = None


class EngineFailed(Exception):
    pass


def stop_child() -> None:
    process = child
    if process is not None and process.poll() is None:
        for number in (signal.SIGCONT, signal.SIGKILL):
            try:
                process.send_signal(number)
            except ProcessLookupError:
                break


def terminate(_signum: int, _frame: object) -> NoReturn:
    stop_child()
    raise SystemExit(143)


def run_external(
    job: Job,
    command: list[str],
    read_stdout: Callable[[IO[bytes]], bool],
    on_stderr: Callable[[str], None],
    *,
    terminal: bool = False,
) -> None:
    """Runs an engine binary, paced, reading its stdout with `read_stdout` and each line of its
    stderr with `on_stderr` (or the text of a pseudo-terminal, with `terminal`)."""
    global child
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    if terminal:
        leader, follower = pty.openpty()
        # A wide terminal, so the folders being read are not shortened.
        fcntl.ioctl(follower, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 400, 0, 0))
        env["TERM"] = "xterm"
        stderr: int = follower
    else:
        stderr = subprocess.PIPE
    child = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=stderr, env=env
    )
    if terminal:
        # Only the engine keeps the terminal open: reading it fails once the engine exits.
        os.close(follower)
    tail: list[str] = []

    def read_stderr() -> None:
        if terminal:
            while True:
                try:
                    data = os.read(leader, 65536)
                except OSError:
                    break
                if not data:
                    break
                on_stderr(data.decode("utf-8", "replace"))
            os.close(leader)
            return
        assert child is not None and child.stderr is not None
        for raw in child.stderr:
            line = raw.decode("utf-8", "replace").rstrip("\n")
            tail.append(line)
            del tail[:-TAIL_LINES]
            on_stderr(line)

    reader = threading.Thread(target=read_stderr, name="stderr", daemon=True)
    reader.start()
    pacer = Pacer(child.pid, job.pace)
    pacer.start()
    finished = False
    try:
        assert child.stdout is not None
        finished = read_stdout(child.stdout)
    finally:
        pacer.stop()
        if not finished:
            stop_child()
        child.wait()
        reader.join(timeout=10)
    if not finished:
        detail = " | ".join(line for line in tail[-5:] if line.strip())
        raise EngineFailed(f"exit status {child.returncode}" + (f": {detail}" if detail else ""))


def run(job: Job) -> tuple[Tree, ErrorLog, Progress]:
    errors = ErrorLog()
    if job.engine == "native":
        progress = Progress()
        tree = Tree()
        progress.start()
        scan_native(job, tree, errors, progress)
        return tree, errors, progress
    binary = job.binary
    if not binary:
        raise EngineFailed(f"no binary for {job.engine}")
    if job.engine == "du":
        progress = Progress()
        progress.files = None
        tree = Tree(count_files=False)
        progress.start()
        run_external(
            job,
            du_command(job, binary),
            lambda stream: parse_du(stream, job.root, tree, progress),
            lambda line: du_error(line, job.root, errors),
        )
        return tree, errors, progress
    if job.engine == "ncdu":
        threaded = job.threads > 1
        progress = Progress()
        # With several threads its terminal UI counts files and folders together.
        progress.dirs_in_files = threaded
        tree = Tree()
        progress.start()
        terminal = TerminalProgress(job.root, progress)
        # With several threads the export comes at the end; the terminal UI gives the counters.
        export_progress = Progress(open(os.devnull, "w")) if threaded else progress
        run_external(
            job,
            ncdu_command(job, binary),
            lambda stream: parse_ncdu(stream, job, tree, errors, export_progress),
            terminal.feed if threaded else (lambda line: None),
            terminal=threaded,
        )
        if threaded:
            progress.phase = "finishing"
            progress.dirs = export_progress.dirs
            progress.files = export_progress.files
            progress.bytes = export_progress.bytes
            progress.dirs_in_files = False
        return tree, errors, progress
    if job.engine == "gdu":
        progress = Progress(live=False)
        tree = Tree()
        progress.start()
        ignore_file = None
        patterns = ignore_patterns(job)
        if patterns:
            ignore_file = job.result_path + ".ignore"
            with open(ignore_file, "w", encoding="utf-8") as handle:
                handle.write("\n".join(patterns) + "\n")
        log_file = job.result_path + ".log"
        readers: list[GduReader] = []

        def read(stream: IO[bytes]) -> bool:
            readers.append(parse_gdu(stream, job, tree, errors, progress))
            return readers[-1].finished

        try:
            run_external(
                job, gdu_command(job, binary, ignore_file, log_file), read, lambda line: None
            )
            with open(log_file, encoding="utf-8", errors="replace") as handle:
                gdu_errors(readers[-1], [line for line in handle if "level=" in line], errors)
        finally:
            for leftover in (ignore_file, log_file):
                if leftover and os.path.exists(leftover):
                    os.unlink(leftover)
        return tree, errors, progress
    raise EngineFailed(f"unknown engine {job.engine}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m disksprawl.engines JOB.json", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, terminate)
    job = Job.load(argv[1])
    out = Progress()
    try:
        tree, errors, progress = run(job)
        progress.phase = "finishing"
        result = tree.finalize(job.min_entry_bytes)
        result.write(job.result_path)
        progress.stop()
    except EngineFailed as error:
        out.emit({"type": "error", "code": "engine_failed", "detail": str(error)[:500]})
        return 1
    except FileNotFoundError as error:
        out.emit({"type": "error", "code": "path_unavailable", "detail": str(error)[:500]})
        return 1
    except OSError as error:
        out.emit({"type": "error", "code": "scan_failed", "detail": str(error)[:500]})
        return 1
    except Exception as error:  # noqa: BLE001 - reported to the server, which marks the scan failed
        out.emit({"type": "error", "code": "scan_failed", "detail": repr(error)[:500]})
        return 1
    out.emit({
        "type": "done",
        "total_bytes": result.total_bytes,
        "file_count": result.file_count,
        "dir_count": result.dir_count,
        "entries": len(result.entries),
        "error_count": errors.count,
        "error_sample": errors.sample,
    })
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
