"""The scan queue: one scan at a time, no duplicates, cancellation, failures and restarts."""

import json
import sys
import time
from pathlib import Path

import pytest
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


def test_schedules_never_fire_during_tests(client: TestClient, scan_root: Path) -> None:
    """The tests' scheduler is paused (conftest.py): a schedule that came due in the middle of a
    test, at 03:00 for example, would add a scan to it. Next runs are still reported."""
    directory = add_directory(client, scan_root, cron="* * * * *")
    jobs = client.app.state.scheduler.scheduler  # type: ignore[attr-defined]
    jobs.modify_job(f"scan-{directory}", next_run_time=utcnow())
    time.sleep(0.5)
    assert client.get(f"/api/directories/{directory}/scans").json()["items"] == []
    assert client.get(f"/api/directories/{directory}/overview").json()["next_run"] is not None


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


def test_a_done_line_with_text_that_is_not_utf8(client: TestClient, scan_root: Path,
                                                tmp_path: Path) -> None:
    """Whatever bytes a worker's lines hold, its `done` line is read and the scan completes,
    with every text valid for the API."""
    script = tmp_path / "worker.py"
    script.write_text(r"""
import json, sys
job = json.load(open(sys.argv[1]))
with open(job["result_path"], "w") as handle:
    handle.write(json.dumps([0, -1, "", 3000, 3, 1]) + "\n")
out = sys.stdout.buffer
out.write(b'{"type": "progress", "phase": "scanning", "live": true, "dirs": 1, "files": 1,'
          b' "bytes": 1, "current": "caf\xe9 \\udce9"}\n')
out.write(b'{"type": "done", "total_bytes": 3000, "file_count": 3, "dir_count": 1, "entries": 1,'
          b' "error_count": 1, "error_sample": [{"path": "ni\xf1os \\udcf1", "message": "x"}]}\n')
out.flush()
""")
    use_worker(client, [sys.executable, str(script)])
    directory = add_directory(client, scan_root)
    done = scan(client, directory)
    assert done["status"] == "completed", done
    assert done["error_sample"] == [{"path": "ni\ufffdos \\xf1", "message": "x"}]


def test_old_scans_lose_their_folders_after_a_new_one(client: TestClient, scan_root: Path,
                                                      tmp_path: Path) -> None:
    use_worker(client, fake_worker(tmp_path))
    settings = {key: value for key, value in client.get("/api/settings").json().items()
                if key != "timezone"}
    client.put("/api/settings", json={**settings, "keep_scans": 1}, headers=WRITE)
    directory = add_directory(client, scan_root)
    first, second = scan(client, directory), scan(client, directory)
    assert client.get(f"/api/scans/{first['id']}").json()["has_detail"] is False
    assert client.get(f"/api/scans/{second['id']}").json()["has_detail"] is True


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


def test_estimates_compare_folders_and_files_with_the_previous_scan() -> None:
    # The work of the previous scan: 90,000 files and 1,000 folders (the scanned one included),
    # each folder weighing as much as 10 files: 100,000.
    previous = Previous(dir_count=999, file_count=90_000, duration=3600)
    live = Live(1, 1, "native", "apparent", previous=None, dirs=400, files=31_000)
    live.done_bytes = 9 * 10**11
    assert live.estimate(700) == (None, None)
    live.previous = previous
    # 31,000 files and 400 folders: 35,000 of 100,000. The bytes play no part.
    ratio, remaining = live.estimate(700)
    assert ratio == pytest.approx(0.35) and remaining == pytest.approx(1300)
    # Nor does the measure.
    disk = Live(1, 1, "native", "disk", previous=previous, dirs=400, files=31_000)
    assert disk.estimate(700)[0] == pytest.approx(0.35)
    # du counts finished folders and no files, and a du scan as the reference has no files.
    du = Live(1, 1, "du", "apparent", previous=previous, dirs=250)
    assert du.estimate(30)[0] == pytest.approx(0.25)
    after_du = Live(1, 1, "native", "apparent", previous=Previous(999, None, 60), dirs=250,
                    files=90_000)
    assert after_du.estimate(30)[0] == pytest.approx(0.25)
    # ncdu with several threads has one counter of files and folders together.
    threaded = Live(1, 1, "ncdu", "apparent", previous=previous, dirs=None, files=45_500)
    assert threaded.estimate(30)[0] == pytest.approx(0.5)
    # Growth since the previous scan: 99 % is the most shown.
    grown = Live(1, 1, "native", "apparent", previous=previous, dirs=500, files=150_000)
    assert grown.estimate(30)[0] == 0.99
    # Nothing counted yet: no estimate; no time left before 1 % done and 2 seconds.
    assert Live(1, 1, "native", "apparent", previous=previous, files=0).estimate(30) == (None, None)
    assert Live(1, 1, "native", "apparent", previous=previous, dirs=5, files=500).estimate(30)[1] is None
    assert live.estimate(1.5)[1] is None
    # gdu reports nothing until the end; neither does a scan that is saving its results.
    gdu = Live(1, 1, "gdu", "apparent", previous=previous, counters=False)
    assert gdu.estimate(30) == (None, None)
    live.phase = "saving"
    assert live.estimate(30) == (None, None)


@pytest.mark.parametrize("folder_cost", [0, 5, 10, 30])
def test_estimates_follow_the_work_when_large_files_come_first(folder_cost: int) -> None:
    """A generic tree read with its largest files first: 100 folders of one 40 GB video each, then
    900 folders of 100 photos of 4 MB. Reading a file's details takes as long whatever its size,
    and listing a folder as long as `folder_cost` files: from nothing to what a network share
    usually costs. After the videos 92 % of the bytes are read, but at most 3 % of the time has
    passed: the estimate stays within 2 points of the share of time spent."""
    folders = [(1, 40 * 10**9)] * 100 + [(100, 4 * 10**6)] * 900
    file_count = sum(files for files, _ in folders)
    total_time = folder_cost * (len(folders) + 1) + file_count
    live = Live(1, 1, "native", "apparent", previous=Previous(len(folders), file_count, None))
    # The scanned folder first, then its subfolders.
    dirs, read, spent = 1, 0, folder_cost
    for files, size in folders[:-1]:
        dirs, read, spent = dirs + 1, read + files, spent + folder_cost + files
        live.dirs, live.files, live.done_bytes = dirs, read, live.done_bytes + files * size
        ratio, remaining = live.estimate(spent)
        assert ratio == pytest.approx(spent / total_time, abs=0.02), (dirs, read)
        if 0.25 * total_time <= spent <= 0.9 * total_time:
            # The time left, from the pace so far, is within a tenth of the real one.
            assert remaining == pytest.approx(total_time - spent, rel=0.1), (dirs, read)


@pytest.mark.parametrize("tree", [[200_000], [0, 50_000, 50_000, 50_000]])
def test_estimates_of_folders_with_many_files(tree: list[int]) -> None:
    """A folder is counted once its listing ends, so a scan of few folders holding many files spends
    nearly all its time in folders not counted yet: the estimate follows the files there, instead
    of stalling at 50 % (one folder) or lagging behind (three under the scanned one). `tree` lists
    the folders in the order the native engine reads them, with their files; listing one takes as
    long as reading ten files."""
    file_count = sum(tree)
    total_time = 10 * len(tree) + file_count
    live = Live(1, 1, "native", "apparent", previous=Previous(len(tree) - 1, file_count, None))
    dirs = read = spent = 0
    for files in tree:
        for tenth in range(1, 11):
            # The folder is listed, then its files are read a tenth at a time.
            live.dirs, live.files = dirs, read + files * tenth // 10
            ratio, _ = live.estimate(spent + 10 + files * tenth // 10)
            if ratio is not None:
                share = (spent + 10 + files * tenth // 10) / total_time
                assert ratio == pytest.approx(min(share, 0.99), abs=0.01), (dirs, live.files)
        dirs, read, spent = dirs + 1, read + files, spent + 10 + files


def test_the_estimate_through_the_api(client: TestClient, scan_root: Path, tmp_path: Path):
    """The previous scan's folders and files are the reference: not its bytes, which a worker that
    met the largest files first has nearly all read."""
    control = tmp_path / "control"
    control.mkdir()
    script = tmp_path / "worker.py"
    script.write_text(f"""
import json, pathlib, sys, time
job = json.load(open(sys.argv[1]))
control = pathlib.Path({str(control)!r})
plan = control / "progress.json"
while plan.exists() and not (control / "release").exists():
    print(json.dumps({{"type": "progress", "phase": "scanning", "live": True, "current": "x",
                      **json.loads(plan.read_text())}}), flush=True)
    time.sleep(0.05)
with open(job["result_path"], "w") as handle:
    handle.write(json.dumps([0, -1, "", 10**12, 3000, 99]) + "\\n")
print(json.dumps({{"type": "done", "total_bytes": 10**12, "file_count": 3000, "dir_count": 99,
                  "entries": 1, "error_count": 0, "error_sample": []}}), flush=True)
""")
    use_worker(client, [sys.executable, str(script)])
    directory = add_directory(client, scan_root)
    # The reference: 3,000 files, 100 folders with the scanned one, and 1 TB.
    assert scan(client, directory)["status"] == "completed"

    def progress_with(counters: dict[str, int | None]) -> dict[str, object]:
        (control / "progress.json").write_text(json.dumps(counters))
        (control / "release").unlink(missing_ok=True)
        client.post(f"/api/directories/{directory}/scan", headers=WRITE)

        def overview() -> dict[str, object]:
            return client.get(f"/api/directories/{directory}/overview").json()["progress"] or {}

        wait_for(lambda: overview().get("percent") is not None)
        progress = overview()
        (control / "release").touch()
        wait_idle(client)
        return progress

    # 95 % of the bytes, but 1,000 files and 40 folders (as much as 400 files): 1,400 of 4,000.
    progress = progress_with({"dirs": 40, "files": 1000, "bytes": 95 * 10**10})
    assert progress["percent"] == 0.35
    # ncdu with several threads: one counter of files and folders, and no folder count.
    progress = progress_with({"dirs": None, "files": 1550, "bytes": 10**11})
    assert progress["percent"] == 0.5 and progress["dirs"] == 0
