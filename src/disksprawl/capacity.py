"""Volume capacity with statvfs (like `df`): fast, it never walks the folders.

A network share whose server does not answer can block statvfs, and resolving the path, for a long
time (an NFS `hard` mount waits until the server is back). So a measurement runs in a thread of its
own with a time limit, and never inside a database transaction: SQLite has a single writer, which
would keep every other write waiting meanwhile.
"""

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .db import CapacitySample, utcnow
from .paths import resolve

log = logging.getLogger(__name__)

# Seconds a volume gets to answer.
TIMEOUT = 10.0
# Folders whose measurement is still blocked: they are not measured again until it returns, so a
# hung mount never piles up threads.
_blocked: set[str] = set()
_blocked_lock = threading.Lock()


@dataclass(frozen=True)
class Capacity:
    total: int
    used: int
    free: int
    device: str


def measure(path: Path) -> Capacity:
    stats = os.statvfs(path)
    total = stats.f_blocks * stats.f_frsize
    used = (stats.f_blocks - stats.f_bfree) * stats.f_frsize
    # Space available to unprivileged users, as df reports it (excludes reserved blocks).
    free = stats.f_bavail * stats.f_frsize
    return Capacity(total, used, free, str(os.stat(path).st_dev))


def measure_folder(path: str, scan_root: Path, directory_id: int | None = None) -> Capacity | None:
    """The capacity of the volume of a directory's folder; None when it is unavailable or does not
    answer within TIMEOUT seconds."""
    with _blocked_lock:
        if path in _blocked:
            return None
        _blocked.add(path)
    found: list[Capacity] = []

    def run() -> None:
        try:
            found.append(measure(resolve(path, scan_root)))
        except Exception:  # noqa: BLE001 - an unavailable volume simply has no capacity
            pass
        finally:
            with _blocked_lock:
                _blocked.discard(path)

    worker = threading.Thread(target=run, name="capacity", daemon=True)
    worker.start()
    worker.join(TIMEOUT)
    if worker.is_alive():
        log.warning("The volume of directory %s did not answer within %s s", directory_id, TIMEOUT)
        return None
    return found[0] if found else None


def store(db: Session, directory_id: int, capacity: Capacity) -> CapacitySample:
    row = CapacitySample(
        directory_id=directory_id,
        measured_at=utcnow(),
        total=capacity.total,
        used=capacity.used,
        free=capacity.free,
        device=capacity.device,
    )
    db.add(row)
    db.flush()
    return row


def percent(used: int, free: int) -> float:
    """Used share of the space available, as df computes it (reserved blocks left out)."""
    available = used + free
    return round(100 * used / available, 1) if available else 0.0
