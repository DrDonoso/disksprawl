"""General settings, stored as key/value pairs; every key has a default and limits."""

from typing import Any

from sqlalchemy.orm import Session

from .db import Setting
from .schemas import MAX_SEEN_NOTES

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
    # Days each scan's log file is kept after the scan ends (scan_logs.py).
    "scan_log_days": 30,
}
LIMITS: dict[str, tuple[int, int]] = {
    "capacity_interval_minutes": (1, 1440),
    "keep_scans": (1, 1000),
    "min_entry_mib": (1, 1024),
    "warn_percent": (1, 99),
    "critical_percent": (2, 100),
    "scan_log_days": (1, 3650),
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


# "What's new" notes already read: one list for the whole install, in its own key (not in DEFAULTS).
SEEN_NOTES_KEY = "whats_new_seen"


def load_seen_notes(db: Session) -> list[str] | None:
    """The ids read so far; None when nothing is recorded (a new install: no news to catch up on)."""
    row = db.get(Setting, SEEN_NOTES_KEY)
    if row is None or not isinstance(row.value, list):
        return None
    return sorted(item for item in row.value if isinstance(item, str))


def mark_notes_seen(db: Session, ids: list[str]) -> list[str]:
    """Adds notes to the read ones: sorted, the newest MAX_SEEN_NOTES kept (ids start with a date)."""
    merged = sorted({*(load_seen_notes(db) or []), *ids})[-MAX_SEEN_NOTES:]
    row = db.get(Setting, SEEN_NOTES_KEY)
    if row is None:
        db.add(Setting(key=SEEN_NOTES_KEY, value=merged))
    else:
        row.value = merged
    db.flush()
    return merged
