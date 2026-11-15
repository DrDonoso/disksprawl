"""Volume capacity with statvfs (like `df`): fast, it never walks the folders."""

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from .db import CapacitySample, Directory, utcnow
from .paths import resolve


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


def sample(db: Session, directory: Directory, scan_root: Path) -> CapacitySample | None:
    """Measures and stores the capacity of a directory's volume; None when it is unavailable."""
    try:
        capacity = measure(resolve(directory.path, scan_root))
    except Exception:  # noqa: BLE001 - an unavailable volume simply has no sample
        return None
    row = CapacitySample(
        directory_id=directory.id,
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
