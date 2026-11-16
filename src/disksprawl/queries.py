"""Read models of the API: directory summaries, live progress, folder trees and changes."""

from datetime import datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .capacity import percent
from .db import CapacitySample, Directory, PathNode, Scan, utcnow
from .errors import AppError
from .scanning import Live, Scanner, elapsed_since
from .schemas import (
    CapacityHistoryOut,
    CapacityOut,
    CapacityPointOut,
    DiffChangeOut,
    DiffChildOut,
    DiffOut,
    DirectoryOut,
    DirectoryRef,
    DirectorySummaryOut,
    ProgressOut,
    ScanOut,
    TreeChildOut,
    TreeOut,
)

BIGGEST_CHANGES = 10
# A folder is listed instead of its parent when it explains this share of the parent's change.
SPECIFIC_SHARE = 0.9
MAX_CAPACITY_POINTS = 400


def progress_out(scan: Scan, live: Live | None, positions: dict[int, int]) -> ProgressOut:
    options = scan.options
    common = {
        "scan_id": scan.id,
        "directory_id": scan.directory_id,
        "queued_at": scan.queued_at,
        "engine": scan.engine,
        "pace": options.get("pace", "max"),
        "threads": int(options.get("threads", 1)),
    }
    if scan.status == "queued" or live is None or live.scan_id != scan.id:
        previous = None
        if live is not None and live.scan_id == scan.id and live.previous:
            previous = live.previous.duration
        return ProgressOut(
            **common,
            status="queued" if scan.status == "queued" else "running",
            phase="queued" if scan.status == "queued" else "scanning",
            position=positions.get(scan.id),
            started_at=scan.started_at,
            elapsed_seconds=round(elapsed_since(scan.started_at), 1),
            counters=False,
            dirs=0,
            files=None,
            bytes=0,
            current=None,
            percent=None,
            remaining_seconds=None,
            previous_duration_seconds=previous,
        )
    elapsed = elapsed_since(scan.started_at)
    ratio, remaining = live.estimate(elapsed)
    phase = live.phase if live.phase in ("scanning", "finishing", "saving") else "scanning"
    return ProgressOut(
        **common,
        status="running",
        phase=phase,
        position=None,
        started_at=scan.started_at,
        elapsed_seconds=round(elapsed, 1),
        counters=live.counters,
        # Folders counted with the files (ncdu with several threads) show no count of their own.
        dirs=live.dirs if live.dirs is not None else 0,
        files=live.files,
        bytes=live.done_bytes,
        current=live.current,
        percent=round(ratio, 4) if ratio is not None else None,
        remaining_seconds=round(remaining) if remaining is not None else None,
        previous_duration_seconds=live.previous.duration if live.previous else None,
    )


def active_progress(db: Session, scanner: Scanner) -> list[ProgressOut]:
    positions = scanner.queue_positions(db)
    live = scanner.live()
    scans = db.scalars(
        select(Scan)
        .where(Scan.status.in_(("queued", "running")))
        .order_by(Scan.status.desc(), Scan.queued_at, Scan.id)
    ).all()
    return [progress_out(scan, live, positions) for scan in scans]


def latest_capacity(db: Session) -> dict[int, CapacitySample]:
    latest_ids = select(func.max(CapacitySample.id)).group_by(CapacitySample.directory_id)
    samples = db.scalars(select(CapacitySample).where(CapacitySample.id.in_(latest_ids))).all()
    return {sample.directory_id: sample for sample in samples}


def summaries(
    db: Session,
    scanner: Scanner,
    next_run: dict[int, datetime | None],
    directory_id: int | None = None,
) -> list[DirectorySummaryOut]:
    query = select(Directory).order_by(Directory.position, Directory.id)
    if directory_id is not None:
        query = query.where(Directory.id == directory_id)
    directories = db.scalars(query).all()
    if not directories:
        return []
    ids = [directory.id for directory in directories]
    names = {
        directory.id: directory.name for directory in db.scalars(select(Directory)).all()
    }
    last_completed = _latest_scans(db, ids, ("completed",))
    last_finished = _latest_scans(db, ids, ("completed", "failed", "cancelled", "interrupted"))
    progress = {item.directory_id: item for item in active_progress(db, scanner)}
    samples = latest_capacity(db)
    devices: dict[str, list[int]] = {}
    for sample in samples.values():
        devices.setdefault(sample.device, []).append(sample.directory_id)
    result = []
    for directory in directories:
        completed = last_completed.get(directory.id)
        finished = last_finished.get(directory.id)
        attempt = finished if finished is not None and finished.status != "completed" else None
        latest = samples.get(directory.id)
        capacity = None
        if latest is not None:
            capacity = CapacityOut(
                total=latest.total,
                used=latest.used,
                free=latest.free,
                percent=percent(latest.used, latest.free),
                measured_at=latest.measured_at,
                shared_with=[
                    DirectoryRef(id=other, name=names[other])
                    for other in devices.get(latest.device, [])
                    if other != directory.id and other in names
                ],
            )
        run = next_run.get(directory.id)
        result.append(
            DirectorySummaryOut(
                directory=DirectoryOut.model_validate(directory),
                last_scan=ScanOut.model_validate(completed) if completed else None,
                last_attempt=ScanOut.model_validate(attempt) if attempt else None,
                progress=progress.get(directory.id),
                capacity=capacity,
                next_run=run,
            )
        )
    return result


def _latest_scans(db: Session, ids: list[int], statuses: tuple[str, ...]) -> dict[int, Scan]:
    ranked = (
        select(
            Scan.id,
            func.row_number()
            .over(
                partition_by=Scan.directory_id,
                order_by=(Scan.finished_at.desc(), Scan.id.desc()),
            )
            .label("rank"),
        )
        .where(Scan.directory_id.in_(ids), Scan.status.in_(statuses))
        .subquery()
    )
    scans = db.scalars(
        select(Scan).join(ranked, ranked.c.id == Scan.id).where(ranked.c.rank == 1)
    ).all()
    return {scan.directory_id: scan for scan in scans}


def get_directory(db: Session, directory_id: int) -> Directory:
    directory = db.get(Directory, directory_id)
    if directory is None:
        raise AppError("directory_not_found", 404)
    return directory


def split_path(path: str) -> list[str]:
    parts = [part for part in path.split("/") if part]
    if any(part in (".", "..") for part in parts):
        raise AppError("path_not_found", 404)
    return parts


def find_path(db: Session, directory_id: int, path: str) -> int:
    node = db.scalar(
        select(PathNode.id).where(
            PathNode.directory_id == directory_id, PathNode.parent_id.is_(None)
        )
    )
    for name in split_path(path):
        if node is None:
            break
        node = db.scalar(
            select(PathNode.id).where(
                PathNode.directory_id == directory_id,
                PathNode.parent_id == node,
                PathNode.name == name,
            )
        )
    if node is None:
        raise AppError("path_not_found", 404)
    return node


def detailed_scan(db: Session, directory_id: int, scan_id: int | None) -> Scan:
    """A completed scan of the directory that still has its folders (the latest by default)."""
    if scan_id is not None:
        scan = db.get(Scan, scan_id)
        if scan is None or scan.directory_id != directory_id:
            raise AppError("scan_not_found", 404)
        if not scan.has_detail:
            raise AppError("scan_without_detail", 404)
        return scan
    scan = db.scalars(
        select(Scan)
        .where(Scan.directory_id == directory_id, Scan.has_detail.is_(True))
        .order_by(Scan.finished_at.desc(), Scan.id.desc())
    ).first()
    if scan is None:
        raise AppError("no_scan_yet", 404)
    return scan


def join_path(parent: str, name: str) -> str:
    return f"{parent}/{name}" if parent else name


def tree(db: Session, directory_id: int, scan_id: int | None, path: str) -> TreeOut:
    get_directory(db, directory_id)
    scan = detailed_scan(db, directory_id, scan_id)
    clean = "/".join(split_path(path))
    node = find_path(db, directory_id, clean)
    own = db.execute(
        text("SELECT size, files, dirs FROM scan_entries WHERE scan_id = :scan AND path_id = :node"),
        {"scan": scan.id, "node": node},
    ).first()
    if own is None:
        raise AppError("path_not_found", 404)
    rows = db.execute(
        text(
            """
            SELECT p.name, e.size, e.files, e.dirs,
                   EXISTS (SELECT 1 FROM paths c
                           JOIN scan_entries ce ON ce.path_id = c.id AND ce.scan_id = :scan
                           WHERE c.parent_id = p.id) AS has_children
            FROM paths p JOIN scan_entries e ON e.path_id = p.id AND e.scan_id = :scan
            WHERE p.parent_id = :node
            ORDER BY e.size DESC, p.name
            """
        ),
        {"scan": scan.id, "node": node},
    ).all()
    children = [
        TreeChildOut(
            name=name,
            path=join_path(clean, name),
            size=size,
            files=files,
            dirs=dirs,
            has_children=bool(has_children),
        )
        for name, size, files, dirs, has_children in rows
    ]
    return TreeOut(
        scan=ScanOut.model_validate(scan),
        path=clean,
        size=own.size,
        files=own.files,
        dirs=own.dirs,
        other_size=max(own.size - sum(child.size for child in children), 0),
        children=children,
    )


def previous_detailed(db: Session, scan: Scan) -> Scan | None:
    return db.scalars(
        select(Scan)
        .where(
            Scan.directory_id == scan.directory_id,
            Scan.has_detail.is_(True),
            Scan.id != scan.id,
            Scan.finished_at <= scan.finished_at,
        )
        .order_by(Scan.finished_at.desc(), Scan.id.desc())
    ).first()


def diff(
    db: Session, directory_id: int, from_id: int | None, to_id: int | None, path: str
) -> DiffOut:
    get_directory(db, directory_id)
    after_scan = detailed_scan(db, directory_id, to_id)
    if from_id is not None:
        before_scan = detailed_scan(db, directory_id, from_id)
    else:
        found = previous_detailed(db, after_scan)
        if found is None:
            raise AppError("no_previous_scan", 404)
        before_scan = found
    clean = "/".join(split_path(path))
    node = find_path(db, directory_id, clean)
    scans = {"before": before_scan.id, "after": after_scan.id}
    own = db.execute(
        text(
            """
            SELECT (SELECT size FROM scan_entries WHERE scan_id = :before AND path_id = :node),
                   (SELECT size FROM scan_entries WHERE scan_id = :after AND path_id = :node)
            """
        ),
        {**scans, "node": node},
    ).one()
    if own[0] is None and own[1] is None:
        raise AppError("path_not_found", 404)
    rows = db.execute(
        text(
            """
            SELECT p.name, a.size, b.size,
                   EXISTS (SELECT 1 FROM paths c
                           JOIN scan_entries ce ON ce.path_id = c.id
                                AND ce.scan_id IN (:before, :after)
                           WHERE c.parent_id = p.id) AS has_children
            FROM paths p
            LEFT JOIN scan_entries a ON a.path_id = p.id AND a.scan_id = :before
            LEFT JOIN scan_entries b ON b.path_id = p.id AND b.scan_id = :after
            WHERE p.parent_id = :node AND (a.size IS NOT NULL OR b.size IS NOT NULL)
            """
        ),
        {**scans, "node": node},
    ).all()
    children = sorted(
        (
            DiffChildOut(
                name=name,
                path=join_path(clean, name),
                before=before,
                after=after,
                has_children=bool(has_children),
            )
            for name, before, after, has_children in rows
        ),
        key=lambda child: (-max(child.before or 0, child.after or 0), child.name),
    )
    before_total, after_total = own[0], own[1]
    return DiffOut(
        from_scan=ScanOut.model_validate(before_scan),
        to_scan=ScanOut.model_validate(after_scan),
        path=clean,
        before=before_total,
        after=after_total,
        other_before=max((before_total or 0) - sum(c.before or 0 for c in children), 0),
        other_after=max((after_total or 0) - sum(c.after or 0 for c in children), 0),
        children=children,
        biggest=biggest_changes(db, node, clean, before_scan.id, after_scan.id),
    )


def biggest_changes(
    db: Session, node: int, base: str, before: int, after: int
) -> list[DiffChangeOut]:
    rows = db.execute(
        text(
            """
            WITH RECURSIVE sub(id) AS (
                SELECT :node UNION ALL SELECT p.id FROM paths p JOIN sub ON p.parent_id = sub.id
            )
            SELECT p.id, p.parent_id, p.name, a.size, b.size
            FROM sub JOIN paths p ON p.id = sub.id
            LEFT JOIN scan_entries a ON a.path_id = p.id AND a.scan_id = :before
            LEFT JOIN scan_entries b ON b.path_id = p.id AND b.scan_id = :after
            WHERE a.size IS NOT NULL OR b.size IS NOT NULL
            """
        ),
        {"node": node, "before": before, "after": after},
    ).all()
    info = {row[0]: row for row in rows}
    delta = {row[0]: (row[4] or 0) - (row[3] or 0) for row in rows}
    children: dict[int, list[int]] = {}
    for identifier, parent, *_ in rows:
        if parent in info:
            children.setdefault(parent, []).append(identifier)

    def explained_by_child(identifier: int) -> bool:
        change = delta[identifier]
        return any(
            delta[child] * change > 0 and abs(delta[child]) >= SPECIFIC_SHARE * abs(change)
            for child in children.get(identifier, [])
        )

    def path_of(identifier: int) -> str:
        names = []
        while identifier != node:
            names.append(info[identifier][2])
            identifier = info[identifier][1]
        return "/".join(filter(None, [base, *reversed(names)]))

    candidates = sorted(
        (i for i in delta if i != node and delta[i] and not explained_by_child(i)),
        key=lambda i: -abs(delta[i]),
    )
    return [
        DiffChangeOut(path=path_of(i), before=info[i][3], after=info[i][4])
        for i in candidates[:BIGGEST_CHANGES]
    ]


def scans(db: Session, directory_id: int, days: int | None, limit: int) -> list[ScanOut]:
    get_directory(db, directory_id)
    query = select(Scan).where(Scan.directory_id == directory_id)
    if days is not None:
        since = utcnow() - timedelta(days=days)
        query = query.where((Scan.finished_at >= since) | Scan.finished_at.is_(None))
    rows = db.scalars(query.order_by(Scan.queued_at.desc(), Scan.id.desc()).limit(limit)).all()
    return [ScanOut.model_validate(scan) for scan in rows]


def capacity_history(db: Session, directory_id: int, days: int) -> CapacityHistoryOut:
    get_directory(db, directory_id)
    since = utcnow() - timedelta(days=days)
    samples = db.scalars(
        select(CapacitySample)
        .where(CapacitySample.directory_id == directory_id, CapacitySample.measured_at >= since)
        .order_by(CapacitySample.measured_at)
    ).all()
    # Evenly thinned to a few hundred points: enough for a chart, light on the browser.
    step = max(1, -(-len(samples) // MAX_CAPACITY_POINTS))
    chosen = list(samples[::step])
    if samples and chosen[-1] is not samples[-1]:
        chosen.append(samples[-1])
    return CapacityHistoryOut(
        points=[
            CapacityPointOut(
                measured_at=sample.measured_at,
                total=sample.total,
                used=sample.used,
                free=sample.free,
                percent=percent(sample.used, sample.free),
            )
            for sample in chosen
        ]
    )
