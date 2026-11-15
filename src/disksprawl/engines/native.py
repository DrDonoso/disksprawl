"""The native engine: `os.scandir` with N threads, resting between folders according to the pace.

Every entry is counted with `lstat` semantics, like GNU du: symbolic links are never followed, hard
links count once, folders count their own blocks on disk but nothing in apparent size, and with
`one_filesystem` folders on another device are skipped (`du -x`).
"""

import os
import queue
import threading
from collections.abc import Callable
from typing import Any

from .common import (
    PAUSE_SHARE,
    Clock,
    ErrorLog,
    Excluder,
    Job,
    Progress,
    Sleep,
    Tree,
    entry_size,
    folder_size,
    monotonic,
    os_error_message,
)


def rest_ratio(pace: str) -> float:
    """Seconds of rest per second of work, so that resting takes the pace's share of the time."""
    share = PAUSE_SHARE[pace]
    return share / (1 - share)


def scan_native(
    job: Job,
    tree: Tree,
    errors: ErrorLog,
    progress: Progress,
    *,
    sleep: Sleep | None = None,
    clock: Clock = monotonic,
    scandir: Callable[[str], Any] = os.scandir,
) -> None:
    root = job.root
    excluded = Excluder(job.excludes)
    measure = job.measure
    ratio = rest_ratio(job.pace)
    stop = threading.Event()
    rest = sleep or (lambda seconds: stop.wait(seconds) and None)
    root_stat = os.lstat(root)
    root_device = root_stat.st_dev
    # Each hard-linked file counts once, in the first of its folders by path, whatever order the
    # threads read them in: the same tree always gives the same folder sizes.
    links: dict[tuple[int, int], tuple[str, int]] = {}
    links_lock = threading.Lock()
    work: queue.Queue[tuple[int, str, str]] = queue.Queue()
    root_index = tree.add(-1, "", folder_size(root_stat, measure))
    progress.bytes = folder_size(root_stat, measure)
    work.put((root_index, root, ""))
    failures: list[BaseException] = []

    def visit(folder: int, path: str, relative_path: str) -> None:
        progress.current = relative_path
        try:
            entries = scandir(path)
        except OSError as error:
            errors.add(relative_path, os_error_message(error))
            return
        with entries:
            while True:
                try:
                    entry = next(entries)
                except StopIteration:
                    break
                except OSError as error:
                    errors.add(relative_path, os_error_message(error))
                    break
                name = entry.name
                if excluded(name):
                    continue
                child_path = relative_path + "/" + name if relative_path else name
                try:
                    is_dir = entry.is_dir(follow_symlinks=False)
                    stat = entry.stat(follow_symlinks=False)
                except OSError as error:
                    errors.add(child_path, os_error_message(error))
                    continue
                if is_dir:
                    if job.one_filesystem and stat.st_dev != root_device:
                        continue
                    size = folder_size(stat, measure)
                    child = tree.add(folder, name, size)
                    progress.bytes += size
                    work.put((child, entry.path, child_path))
                    continue
                size = entry_size(stat, measure)
                if stat.st_nlink > 1:
                    key = (stat.st_dev, stat.st_ino)
                    with links_lock:
                        holder = links.get(key)
                        if holder is None:
                            links[key] = (relative_path, folder)
                        else:
                            if relative_path < holder[0]:
                                tree.add_bytes(holder[1], -size)
                                links[key] = (relative_path, folder)
                            else:
                                size = 0
                            progress.bytes -= size
                tree.add_file(folder, size)
                progress.bytes += size
                if progress.files is not None:
                    progress.files += 1
        progress.dirs += 1

    def worker() -> None:
        while not stop.is_set():
            try:
                folder, path, relative_path = work.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                started = clock()
                visit(folder, path, relative_path)
                if ratio:
                    rest((clock() - started) * ratio)
            except BaseException as error:  # noqa: BLE001 - reported by the main thread
                failures.append(error)
                stop.set()
            finally:
                work.task_done()

    threads = [
        threading.Thread(target=worker, name=f"scan-{number}", daemon=True)
        for number in range(max(1, job.threads))
    ]
    for thread in threads:
        thread.start()
    # Not work.join(): after a failure the remaining folders are never taken from the queue.
    with work.all_tasks_done:
        while work.unfinished_tasks and not stop.is_set():
            work.all_tasks_done.wait(0.5)
    stop.set()
    for thread in threads:
        thread.join()
    if failures:
        raise failures[0]
