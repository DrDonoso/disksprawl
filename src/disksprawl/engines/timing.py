"""Where the time of a scan goes, for its log (src/disksprawl/scan_logs.py).

The worker measures how long the engine took to read the folders, how much of that time it rested
at the pace, and how long adding up the results took. The Native engine also times every folder it
reads, without its subfolders (listing it and reading the details of its entries): a folder that
takes SLOW_FOLDER_SECONDS or more is reported as it happens, and the summary lists the slowest
folders and the reading time of the first-level folders, each with its largest subfolders.
"""

import heapq
import threading
from array import array
from collections.abc import Callable
from typing import Any

from .common import Tree

# A folder that takes at least this long to read is reported as it happens.
SLOW_FOLDER_SECONDS = 5.0
# Slow folders reported one by one; the summary lists the slowest ones anyway.
MAX_REPORTED_SLOW_FOLDERS = 1_000
# The slowest folders of the summary, its first-level folders by reading time, and the subfolders
# shown under each of them: those with at least MIN_SHARE of the reading time.
SLOWEST_FOLDERS = 20
TOP_FOLDERS = 15
TOP_SUBFOLDERS = 5
MIN_SHARE = 0.01

# Receives each slow folder: its path (relative to the scanned folder), seconds and entries.
Report = Callable[[str, float, int], None]


class Timing:
    def __init__(self, report: Report | None = None) -> None:
        self.report = report
        # Threads that rest in parallel: their rest, added up, divided by them is the time lost.
        self.threads = 1
        self._rest = 0.0
        # Seconds each folder took to read, by its index in the tree (Native only).
        self._seconds: array[float] | None = None
        # The slowest folders so far: a min-heap of (seconds, index, path, entries).
        self._slowest: list[tuple[float, int, str, int]] = []
        self._reported = 0
        self._lock = threading.Lock()

    def rested(self, seconds: float) -> None:
        """Rest at the pace, by one of the threads."""
        with self._lock:
            self._rest += seconds

    def folder(self, index: int, path: str, seconds: float, entries: int) -> None:
        """The folder `index` of the tree took `seconds` to read its `entries` files and folders."""
        report = None
        with self._lock:
            times = self._seconds
            if times is None:
                times = self._seconds = array("d")
            if index >= len(times):
                times.frombytes(bytes(times.itemsize * (index + 1 - len(times))))
            times[index] = seconds
            item = (seconds, index, path, entries)
            if len(self._slowest) < SLOWEST_FOLDERS:
                heapq.heappush(self._slowest, item)
            elif item > self._slowest[0]:
                heapq.heapreplace(self._slowest, item)
            if seconds >= SLOW_FOLDER_SECONDS and self._reported < MAX_REPORTED_SLOW_FOLDERS:
                self._reported += 1
                report = self.report
        if report is not None:
            report(path, seconds, entries)

    def summary(self, tree: Tree, scan_seconds: float, finish_seconds: float) -> dict[str, Any]:
        """The times of the `done` message: the walk, its rest at the pace, adding up, and the
        folders when the engine timed them."""
        result: dict[str, Any] = {
            "scan_seconds": round(scan_seconds, 3),
            "rest_seconds": round(self._rest / max(self.threads, 1), 3),
            "finish_seconds": round(finish_seconds, 3),
        }
        if self._seconds is not None and len(tree):
            result.update(self._folders(tree, self._seconds))
        return result

    def _folders(self, tree: Tree, own: "array[float]") -> dict[str, Any]:
        """The slowest folders, and the reading time of the first-level folders with their
        subfolders. A folder is always added to the tree after its parent (the Native engine finds
        it there), so adding each folder's time to its parent's in reverse order sums every
        subtree."""
        count = len(tree)
        parent = tree.parent
        total = array("d", own[:count])
        if len(total) < count:
            total.frombytes(bytes(total.itemsize * (count - len(total))))
        folders = array("q", [1]) * count
        for index in range(count - 1, -1, -1):
            up = parent[index]
            if up >= 0:
                total[up] += total[index]
                folders[up] += folders[index]
        root = next((index for index in range(count) if parent[index] == -1), 0)
        reading = total[root]
        names = tree.names
        first = sorted(
            (index for index in range(count) if parent[index] == root),
            key=lambda index: total[index],
            reverse=True,
        )[:TOP_FOLDERS]
        children: dict[int, list[int]] = {index: [] for index in first}
        for index in range(count):
            siblings = children.get(parent[index])
            if siblings is not None:
                siblings.append(index)

        def item(index: int, path: str) -> dict[str, Any]:
            return {"path": path, "seconds": round(total[index], 3), "folders": folders[index]}

        by_folder = []
        for index in first:
            entry = item(index, names[index])
            largest = sorted(children[index], key=lambda child: total[child], reverse=True)
            entry["subfolders"] = [
                item(child, f"{names[index]}/{names[child]}")
                for child in largest
                if total[child] >= MIN_SHARE * reading
            ][:TOP_SUBFOLDERS]
            by_folder.append(entry)
        return {
            "threads": self.threads,
            "reading_seconds": round(reading, 3),
            # The scanned folder itself: listing it and its files, without its subfolders.
            "own_seconds": round(own[root] if root < len(own) else 0.0, 3),
            "slowest": [
                {"path": path, "seconds": round(seconds, 3), "entries": entries}
                for seconds, _, path, entries in sorted(self._slowest, reverse=True)
            ],
            "folders": by_folder,
        }
