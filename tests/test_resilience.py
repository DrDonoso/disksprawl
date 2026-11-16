"""The app keeps working when storage misbehaves: slow or hung volumes, a busy database, failures
while recording a scan, and very large deletions."""

import threading
import time
from pathlib import Path

import pytest
from conftest import OPTIONS, WRITE, add_directory, fake_worker, record, wait_idle
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError

from disksprawl import capacity, retention, scanning
from disksprawl import scheduler as scheduling
from disksprawl.db import CapacitySample, PathNode, Scan, ScanEntry, transaction, utcnow

LOCKED = OperationalError("INSERT", {}, Exception("database is locked"))


def samples(client: TestClient, directory: int) -> int:
    with client.app.state.sessions() as db:  # type: ignore[attr-defined]
        return int(db.scalar(
            select(func.count()).select_from(CapacitySample)
            .where(CapacitySample.directory_id == directory)
        ) or 0)


def blocking_measure(monkeypatch: pytest.MonkeyPatch, folder: str) -> tuple[
    threading.Event, threading.Event, list[Path]
]:
    """Makes statvfs of `folder` block until released, like a network share that does not answer."""
    measuring, release = threading.Event(), threading.Event()
    calls: list[Path] = []
    real = capacity.measure

    def measure(path: Path) -> capacity.Capacity:
        if path.name == folder:
            calls.append(path)
            measuring.set()
            release.wait(30)
        return real(path)

    monkeypatch.setattr(capacity, "measure", measure)
    return measuring, release, calls


def test_a_slow_volume_never_holds_the_database(client: TestClient, scan_root: Path,
                                                tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    client.app.state.scanner.worker_command = fake_worker(tmp_path)  # type: ignore[attr-defined]
    (scan_root / "fast").mkdir()
    (scan_root / "slow").mkdir()
    fast = add_directory(client, scan_root / "fast")
    slow = add_directory(client, scan_root / "slow")
    before = samples(client, slow)
    measuring, release, _ = blocking_measure(monkeypatch, "slow")
    job = threading.Thread(target=client.app.state.scheduler.measure_capacity)  # type: ignore[attr-defined]
    job.start()
    try:
        assert measuring.wait(10)
        # A write while the slow volume is measured: it does not wait for it.
        started = time.monotonic()
        response = client.post(f"/api/directories/{fast}/scan", headers=WRITE)
        assert response.status_code == 202
        assert time.monotonic() - started < 2
    finally:
        release.set()
        job.join(30)
    assert samples(client, slow) == before + 1
    wait_idle(client)


def test_a_hung_volume_times_out_without_piling_up(client: TestClient, scan_root: Path,
                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    (scan_root / "fine").mkdir()
    (scan_root / "hung").mkdir()
    fine = add_directory(client, scan_root / "fine")
    hung = add_directory(client, scan_root / "hung")
    before = samples(client, fine), samples(client, hung)
    _, release, calls = blocking_measure(monkeypatch, "hung")
    monkeypatch.setattr(capacity, "TIMEOUT", 0.3)
    try:
        for _ in range(3):
            started = time.monotonic()
            client.app.state.scheduler.measure_capacity()  # type: ignore[attr-defined]
            assert time.monotonic() - started < 3
        # The other volume is measured every time; the hung one is not tried again while blocked.
        assert (samples(client, fine), samples(client, hung)) == (before[0] + 3, before[1])
        assert len(calls) == 1
    finally:
        release.set()


def test_a_failing_catch_up_never_stops_the_start(client: TestClient, scan_root: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    add_directory(client, scan_root, cron="* * * * *")
    calls = []

    def locked(*args: object) -> int:
        calls.append(args)
        raise LOCKED

    monkeypatch.setattr(client.app.state.scanner, "enqueue", locked)  # type: ignore[attr-defined]
    client.app.state.scheduler.catch_up()  # type: ignore[attr-defined]
    assert len(calls) == 1


def test_a_scheduled_scan_waits_for_a_busy_database(client: TestClient, scan_root: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    directory = add_directory(client, scan_root)
    calls = []

    def busy_twice(directory_id: int, trigger: str) -> int:
        calls.append((directory_id, trigger))
        if len(calls) < 3:
            raise LOCKED
        return 1

    monkeypatch.setattr(scheduling, "ENQUEUE_RETRY_SECONDS", 0.01)
    monkeypatch.setattr(client.app.state.scanner, "enqueue", busy_twice)  # type: ignore[attr-defined]
    client.app.state.scheduler._scheduled_scan(directory)  # type: ignore[attr-defined]
    assert calls == [(directory, "scheduled")] * 3


def test_the_queue_survives_failures_while_recording_a_scan(
    client: TestClient, scan_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scanner = client.app.state.scanner  # type: ignore[attr-defined]
    scanner.worker_command = fake_worker(tmp_path)
    monkeypatch.setattr(scanning, "RETRY_SECONDS", 0.01)
    (scan_root / "a").mkdir()
    (scan_root / "b").mkdir()
    first = add_directory(client, scan_root / "a")
    second = add_directory(client, scan_root / "b")
    failures = {"store": 0, "close": 0}
    real_import, real_close = scanning.import_entries, scanning.Scanner._close

    def import_entries(db, scan_id, directory_id, result_path):  # noqa: ANN001, ANN202
        if directory_id == first:
            failures["store"] += 1
            raise LOCKED
        return real_import(db, scan_id, directory_id, result_path)

    def close(self, scan, status, message):  # noqa: ANN001, ANN202
        if status == "failed" and failures["close"] < 2:
            failures["close"] += 1
            raise LOCKED
        real_close(self, scan, status, message)

    monkeypatch.setattr(scanning, "import_entries", import_entries)
    monkeypatch.setattr(scanning.Scanner, "_close", close)
    one = client.post(f"/api/directories/{first}/scan", headers=WRITE).json()["scan_id"]
    two = client.post(f"/api/directories/{second}/scan", headers=WRITE).json()["scan_id"]
    wait_idle(client)
    assert scanner._thread.is_alive()
    failed = client.get(f"/api/scans/{one}").json()
    assert (failed["status"], failed["message"]) == ("failed", "internal_error")
    assert client.get(f"/api/scans/{two}").json()["status"] == "completed"
    assert failures == {"store": scanning.STORE_ATTEMPTS, "close": 2}


def test_scans_left_running_never_block_the_queue(client: TestClient, scan_root: Path,
                                                  tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    scanner = client.app.state.scanner  # type: ignore[attr-defined]
    scanner.worker_command = fake_worker(tmp_path)
    for name in ("a", "b", "c"):
        (scan_root / name).mkdir()
    a, b, c = (add_directory(client, scan_root / name) for name in ("a", "b", "c"))
    # The worker thread claims nothing meanwhile, so each case below meets the stuck scans.
    real_claim = scanner._claim
    monkeypatch.setattr(scanner, "_claim", lambda: None)

    def left_running(directory: int) -> int:
        with transaction(client.app.state.sessions) as db:  # type: ignore[attr-defined]
            scan = Scan(directory_id=directory, status="running", trigger="manual",
                        engine="native", options=OPTIONS, queued_at=utcnow(),
                        started_at=utcnow(), error_count=0, error_sample=[], has_detail=False)
            db.add(scan)
            db.flush()
            return scan.id

    stuck = {directory: left_running(directory) for directory in (a, b, c)}
    # Nothing runs a stuck scan: cancelling it ends it at once.
    assert client.post(f"/api/scans/{stuck[a]}/cancel", headers=WRITE).json()["status"] == (
        "cancelled"
    )
    # "Scan now" queues a new scan instead of answering with the stuck one.
    fresh = client.post(f"/api/directories/{b}/scan", headers=WRITE).json()["scan_id"]
    assert fresh not in stuck.values()
    monkeypatch.setattr(scanner, "_claim", real_claim)
    scanner._wake.set()
    wait_idle(client)
    statuses = {
        scan_id: client.get(f"/api/scans/{scan_id}").json()["status"]
        for scan_id in (*stuck.values(), fresh)
    }
    # The next claim found the last one.
    assert statuses == {stuck[a]: "cancelled", stuck[b]: "failed", stuck[c]: "failed",
                        fresh: "completed"}


def test_deleting_a_directory_goes_in_small_batches(client: TestClient, scan_root: Path,
                                                    tmp_path: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    (scan_root / "kept").mkdir()
    (scan_root / "gone").mkdir()
    kept = add_directory(client, scan_root / "kept")
    gone = add_directory(client, scan_root / "gone")
    for days in (3, 2, 1):
        record(client, gone, {"": 1000, "a": 600, "a/b": 500, "a/b/c": 400, "d": 300},
               tmp_path, days_ago=days)
    record(client, kept, {"": 10, "x": 5}, tmp_path)
    monkeypatch.setattr(retention, "BATCH", 2)
    transactions = []
    real = retention.transaction

    def counted(sessions):  # noqa: ANN001, ANN202
        transactions.append(1)
        return real(sessions)

    monkeypatch.setattr(retention, "transaction", counted)
    assert client.delete(f"/api/directories/{gone}", headers=WRITE).status_code == 204
    # 15 folder rows, 5 paths, a capacity sample and 3 scans, two rows at a time.
    assert len(transactions) > 10
    with client.app.state.sessions() as db:  # type: ignore[attr-defined]
        def count(model, *where):  # noqa: ANN001, ANN202
            return db.scalar(select(func.count()).select_from(model).where(*where))

        assert count(Scan, Scan.directory_id == gone) == 0
        assert count(PathNode, PathNode.directory_id == gone) == 0
        assert count(CapacitySample, CapacitySample.directory_id == gone) == 0
        assert count(ScanEntry) == 2
    assert client.get(f"/api/directories/{gone}/overview").status_code == 404
    assert client.get(f"/api/directories/{kept}/tree").json()["size"] == 10
