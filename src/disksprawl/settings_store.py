"""General settings, stored as key/value pairs; every key has a default and limits."""

from typing import Any

from sqlalchemy.orm import Session

from .db import Setting

DEFAULTS: dict[str, int] = {
    # Minutes between measurements of each volume's capacity.
    "capacity_interval_minutes": 15,
    # Completed scans of each directory that keep their folders (older ones keep the summary).
    "keep_scans": 30,
    # Folders smaller than this (MiB) are not stored; they count in their parent's total.
    "min_entry_mib": 1,
    # Volume usage that shows a warning, and a critical alert.
    "warn_percent": 80,
    "critical_percent": 90,
}
LIMITS: dict[str, tuple[int, int]] = {
    "capacity_interval_minutes": (1, 1440),
    "keep_scans": (1, 1000),
    "min_entry_mib": (1, 1024),
    "warn_percent": (1, 99),
    "critical_percent": (2, 100),
}


def load(db: Session) -> dict[str, int]:
    values = dict(DEFAULTS)
    for row in db.query(Setting).filter(Setting.key.in_(DEFAULTS)):
        value: Any = row.value
        if isinstance(value, int) and not isinstance(value, bool):
            low, high = LIMITS[row.key]
            values[row.key] = min(max(value, low), high)
    return values


def save(db: Session, values: dict[str, int]) -> dict[str, int]:
    for key, value in values.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
    db.flush()
    return load(db)
