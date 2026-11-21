"""Deleting stored scans in small batches.

A large directory keeps millions of folder rows, and SQLite has a single writer: deleting them in
one transaction would make every other write (a sign-in, "Scan now", the scheduler, the scan queue)
wait for it, and fail after the busy timeout. Each batch is a short transaction of its own.
"""

from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from .db import Directory, Scan, transaction

BATCH = 20_000


def delete_in_batches(
    sessions: sessionmaker[Session], statement: str, params: dict[str, object], batch: int
) -> int:
    """Runs `DELETE … WHERE rowid IN (SELECT … LIMIT :batch)` until a batch is not full."""
    total = 0
    while True:
        with transaction(sessions) as db:
            result = db.execute(text(statement), {**params, "batch": batch})
            deleted = cast("CursorResult[Any]", result).rowcount
        total += deleted
        if deleted < batch:
            return total


def delete_entries(sessions: sessionmaker[Session], scan_id: int, batch: int | None = None) -> None:
    delete_in_batches(
        sessions,
        "DELETE FROM scan_entries WHERE rowid IN "
        "(SELECT rowid FROM scan_entries WHERE scan_id = :scan LIMIT :batch)",
        {"scan": scan_id},
        batch or BATCH,
    )


def delete_orphan_paths(
    sessions: sessionmaker[Session], directory_id: int, batch: int | None = None
) -> None:
    """Deletes the folders no stored scan has any more. A child always has a higher id than its
    parent (it is stored after it), so deleting the highest ids first never cascades."""
    delete_in_batches(
        sessions,
        "DELETE FROM paths WHERE id IN (SELECT p.id FROM paths AS p"
        " WHERE p.directory_id = :directory"
        " AND NOT EXISTS (SELECT 1 FROM scan_entries AS e WHERE e.path_id = p.id)"
        " ORDER BY p.id DESC LIMIT :batch)",
        {"directory": directory_id},
        batch or BATCH,
    )


def prune(
    sessions: sessionmaker[Session], directory_id: int, keep: int, batch: int | None = None
) -> int:
    """Keeps the folders of the latest `keep` completed scans; older scans keep their summary.
    Returns how many scans lost their folders."""
    with sessions() as db:
        detailed = db.scalars(
            select(Scan.id)
            .where(Scan.directory_id == directory_id, Scan.has_detail.is_(True))
            .order_by(Scan.finished_at.desc(), Scan.id.desc())
        ).all()
    expired = list(detailed[keep:])
    if not expired:
        return 0
    # The scans lose their detail before their rows go, so nothing reads a half-deleted scan.
    with transaction(sessions) as db:
        db.execute(update(Scan).where(Scan.id.in_(expired)).values(has_detail=False))
    for scan_id in expired:
        delete_entries(sessions, scan_id, batch)
    delete_orphan_paths(sessions, directory_id, batch)
    return len(expired)


def delete_directory(
    sessions: sessionmaker[Session], directory_id: int, batch: int | None = None
) -> None:
    """Deletes everything stored about a directory, its largest tables first, in batches."""
    with transaction(sessions) as db:
        db.execute(update(Scan).where(Scan.directory_id == directory_id).values(has_detail=False))
    with sessions() as db:
        scan_ids = db.scalars(select(Scan.id).where(Scan.directory_id == directory_id)).all()
    for scan_id in scan_ids:
        delete_entries(sessions, scan_id, batch)
    for statement in (
        "DELETE FROM paths WHERE id IN (SELECT id FROM paths WHERE directory_id = :directory"
        " ORDER BY id DESC LIMIT :batch)",
        "DELETE FROM capacity_samples WHERE id IN (SELECT id FROM capacity_samples"
        " WHERE directory_id = :directory LIMIT :batch)",
        "DELETE FROM scans WHERE id IN (SELECT id FROM scans WHERE directory_id = :directory"
        " LIMIT :batch)",
    ):
        delete_in_batches(sessions, statement, {"directory": directory_id}, batch or BATCH)
    with transaction(sessions) as db:
        db.execute(delete(Directory).where(Directory.id == directory_id))
