"""The scan queue: one scan at a time, no duplicates, cancellation, failures and restarts."""

import time
from pathlib import Path

from conftest import OPTIONS, WRITE, add_directory, fake_worker, scan, wait_idle
from fastapi.testclient import TestClient

from disksprawl.app import create_app
from disksprawl.config import Settings
from disksprawl.db import Scan, create_db_engine, migrate, session_factory, transaction, utcnow
from disksprawl.scanning import Live, Previous


def use_worker(client: TestClient, command: list[str]) -> None:
    client.app.state.scanner.worker_command = command  # type: ignore[attr-defined]


def wait_for(predicate, timeout: float = 30) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def test_scans_run_one_at_a_time_in_order(client: TestClient, scan_root: Path, tmp_path: Path):
    use_worker(client, fake_worker(tmp_path, seconds=0.6))
    (scan_root / "first").mkdir()
    (scan_root / "second").mkdir()
    first = add_directory(client, scan_root / "first")
    second = add_directory(client, scan_root / "second")
    one = client.post(f"/api/directories/{first}/scan", headers=WRITE).json()
    two = client.post(f"/api/directories/{second}/scan", headers=WRITE).json()
    assert two["status"] == "queued"
    wait_for(lambda: any(i["status"] == "running" for i in client.get("/api/queue").json()["items"]))
    items = client.get("/api/queue").json()["items"]
    assert [(i["scan_id"], i["status"]) for i in items] == [
        (one["scan_id"], "running"), (two["scan_id"], "queued")
    ]
    assert items[1]["position"] == 1 and items[1]["phase"] == "queued"
    wait_idle(client)
    finished = [client.get(f"/api/scans/{i}").json() for i in (one["scan_id"], two["scan_id"])]
    assert [s["status"] for s in finished] == ["completed", "completed"]
    assert finished[0]["finished_at"] <= finished[1]["started_at"]


def test_a_directory_is_never_queued_twice(client: TestClient, scan_root: Path, tmp_path: Path):
    use_worker(client, fake_worker(tmp_path, seconds=0.5))
    directory = add_directory(client, scan_root)
    first = client.post(f"/api/directories/{directory}/scan", headers=WRITE).json()
    again = client.post(f"/api/directories/{directory}/scan", headers=WRITE).json()
    assert again["scan_id"] == first["scan_id"]
    wait_idle(client)
    assert len(client.get(f"/api/directories/{directory}/scans").json()["items"]) == 1


def test_running_scans_report_live_progress(client: TestClient, scan_root: Path, tmp_path: Path):
    use_worker(client, fake_worker(tmp_path, seconds=1.5))
    directory = add_directory(client, scan_root)
    client.post(f"/api/directories/{directory}/scan", headers=WRITE)

    def counting() -> bool:
        progress = client.get(f"/api/directories/{directory}/overview").json()["progress"]
        return bool(progress and progress["dirs"] > 2)

    wait_for(counting)
    progress = client.get(f"/api/directories/{directory}/overview").json()["progress"]
    assert progress["status"] == "running" and progress["phase"] == "scanning"
    assert progress["counters"] and progress["current"].startswith("folder-")
    # The first scan has nothing to compare with: counters but no percentage.
    assert progress["percent"] is None and progress["remaining_seconds"] is None
    wait_idle(client)
    overview = client.get(f"/api/directories/{directory}/overview").json()
    assert overview["progress"] is None
    assert overview["last_scan"]["total_bytes"] == 3000


def test_cancel_a_running_and_a_queued_scan(client: TestClient, scan_root: Path, tmp_path: Path):
    use_worker(client, fake_worker(tmp_path, seconds=30))
    (scan_root / "a").mkdir()
    (scan_root / "b").mkdir()
    first = add_directory(client, scan_root / "a")
    second = add_directory(client, scan_root / "b")
    running = client.post(f"/api/directories/{first}/scan", headers=WRITE).json()["scan_id"]
    queued = client.post(f"/api/directories/{second}/scan", headers=WRITE).json()["scan_id"]
    wait_for(lambda: client.get(f"/api/scans/{running}").json()["status"] == "running")
    assert client.post(f"/api/scans/{queued}/cancel", headers=WRITE).json()["status"] == "cancelled"
    started = time.monotonic()
    client.post(f"/api/scans/{running}/cancel", headers=WRITE)
    wait_idle(client)
    assert time.monotonic() - started < 10
    scan_out = client.get(f"/api/scans/{running}").json()
    assert scan_out["status"] == "cancelled" and scan_out["has_detail"] is False
    overview = client.get(f"/api/directories/{first}/overview").json()
    assert overview["last_scan"] is None and overview["last_attempt"]["status"] == "cancelled"
    assert client.post(f"/api/scans/{running}/cancel", headers=WRITE).status_code == 409


def test_failures_are_recorded(client: TestClient, scan_root: Path, tmp_path: Path) -> None:
    use_worker(client, fake_worker(tmp_path, fail=True))
    directory = add_directory(client, scan_root)
    failed = scan(client, directory)
    assert failed["status"] == "failed"
    assert failed["message"] == "engine_failed: boom"


def test_a_vanished_folder_fails_the_scan(client: TestClient, scan_root: Path) -> None:
    (scan_root / "gone").mkdir()
    directory = add_directory(client, scan_root / "gone")
    (scan_root / "gone").rmdir()
    assert scan(client, directory)["message"] == "path_not_found"


def test_scans_running_at_shutdown_become_interrupted(settings: Settings, scan_root: Path):
    migrate(settings.database_path)
    sessions = session_factory(create_db_engine(settings.database_path))
    with TestClient(create_app(settings)) as client:
        directory = add_directory(client, scan_root)
    with transaction(sessions) as db:
        db.add(Scan(directory_id=directory, status="running", trigger="manual", engine="native",
                    options=OPTIONS, queued_at=utcnow(), started_at=utcnow(), error_count=0,
                    error_sample=[], has_detail=False))
    with TestClient(create_app(settings)) as client:
        scans = client.get(f"/api/directories/{directory}/scans").json()["items"]
        assert [s["status"] for s in scans] == ["interrupted"]
        overview = client.get(f"/api/directories/{directory}/overview").json()
        assert overview["last_attempt"]["status"] == "interrupted"


def test_estimates_compare_with_the_previous_scan() -> None:
    live = Live(1, 1, "native", "apparent", previous=None)
    live.done_bytes, live.dirs = 500, 10
    assert live.estimate(10) == (None, None)
    live.previous = Previous(total_bytes=1000, dir_count=40, duration=100, measure="apparent")
    ratio, remaining = live.estimate(10)
    assert ratio == 0.5 and remaining == 10
    # Growth since the previous scan never shows 100 % before the end.
    live.done_bytes = 5000
    assert live.estimate(10)[0] == 0.99
    # du counts finished folders: its bytes lag behind.
    du = Live(1, 1, "du", "apparent", previous=live.previous)
    du.done_bytes, du.dirs = 100, 30
    assert du.estimate(30)[0] == 0.75
    # Another measure: bytes are not comparable, folders are.
    disk = Live(1, 1, "native", "disk", previous=live.previous)
    disk.done_bytes, disk.dirs = 900, 4
    assert disk.estimate(30)[0] == 0.1
    # gdu reports nothing until the end; neither does a scan that is saving its results.
    gdu = Live(1, 1, "gdu", "apparent", previous=live.previous, counters=False)
    assert gdu.estimate(30) == (None, None)
    live.phase = "saving"
    assert live.estimate(30) == (None, None)
