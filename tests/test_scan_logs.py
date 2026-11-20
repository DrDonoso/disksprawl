"""The log file of each scan (scan_logs.py): what it says, where it goes, how long it stays and how
it downloads."""

import json
import os
import re
import signal
import stat
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from conftest import OPTIONS, WRITE, add_directory, fake_worker, scan, wait_idle
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from disksprawl import scan_logs, scanning
from disksprawl.app import create_app
from disksprawl.config import Settings
from disksprawl.db import Scan, create_db_engine, migrate, session_factory, transaction, utcnow
from disksprawl.engines import __main__ as worker
from disksprawl.engines.common import Job
from disksprawl.scan_logs import About, ScanLog, ScanLogs, folder_name
from disksprawl.scanning import Scanner

DAY = 86_400
EVENT = re.compile(r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d[+-]\d\d:\d\d  (.+)")


def use_worker(client: TestClient, command: list[str]) -> None:
    client.app.state.scanner.worker_command = command  # type: ignore[attr-defined]


def wait_for(predicate, timeout: float = 30) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("condition not reached")


def logs_dir(client: TestClient) -> Path:
    path: Path = client.app.state.settings.logs_dir  # type: ignore[attr-defined]
    return path


def log_file(client: TestClient, scan_id: object) -> str | None:
    with client.app.state.sessions() as db:  # type: ignore[attr-defined]
        found = db.get(Scan, scan_id)
        assert found is not None
        return found.log_file


def log_path(client: TestClient, scan_id: object) -> Path:
    relative = log_file(client, scan_id)
    assert relative is not None
    return logs_dir(client) / relative


def events(text: str) -> list[str]:
    """The events of a log, without their time: the lines that are not the header or details."""
    return [match[1] for line in text.splitlines() if (match := EVENT.fullmatch(line))]


def age(path: Path, days: float) -> None:
    moment = time.time() - days * DAY
    os.utime(path, (moment, moment))


def stem(moment: object, zone: str = "Etc/UTC") -> str:
    return datetime.fromisoformat(str(moment)).astimezone(ZoneInfo(zone)).strftime("%Y-%m-%d_%H%M")


def test_a_scan_writes_its_log(settings: Settings, scan_root: Path, tmp_path: Path) -> None:
    """In the server's time zone (Nepal: +05:45 all year), with every error the worker reports."""
    nepal = settings.model_copy(update={"tz": "Asia/Kathmandu"})
    (scan_root / "media").mkdir()
    with TestClient(create_app(nepal)) as client:
        use_worker(client, fake_worker(tmp_path, errors=25))
        directory = add_directory(client, scan_root / "media", name="Media", pace="max",
                                  low_priority=False, excludes=[])
        done = scan(client, directory)
        assert done["status"] == "completed" and done["has_log"] is True
        name = f"{stem(done['started_at'], 'Asia/Kathmandu')}.log"
        assert log_file(client, done["id"]) == f"Media/{name}"
        path = logs_dir(client) / "Media" / name
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        assert lines[:9] == [
            f"DiskSprawl development: scan {done['id']}",
            f"Directory: Media (id {directory})",
            f"Folder: {scan_root / 'media'}",
            "Started by: Scan now",
            f"Engine: native (Python {sys.version_info.major}.{sys.version_info.minor})",
            "Options: 2 threads, maximum pace, normal priority, apparent size, one file system",
            "Excluded: nothing",
            "Smallest folder stored: 1 MiB",
            "",
        ]
        queued = datetime.fromisoformat(done["queued_at"]).astimezone(ZoneInfo("Asia/Kathmandu"))
        assert lines[9] == f"{queued.isoformat(sep=' ', timespec='seconds')}  Queued"
        assert lines[9][19:25] == "+05:45"
        logged = events(text)
        assert logged[:2] == ["Queued", "Started"]
        assert logged[2:-1] == [
            f"Error: {scan_root}/media/folder-{number}: Permission denied" for number in range(25)
        ]
        assert logged[-1].startswith("Completed in ")
        details = lines[lines.index(next(line for line in lines if "Completed in" in line)) + 1 :]
        assert details[:5] == [
            "  Size: 2.93 KiB (3,000 bytes)",
            "  Files: 3",
            "  Folders: 1",
            "  Folders stored: 2",
            "  Errors: 25 (listed above)",
        ]
        assert details[5].startswith("  Volume: ") and len(details) == 6
        # Readable by the container's user only, like the rest of the data volume.
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

        response = client.get(f"/api/scans/{done['id']}/log")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        assert response.headers["content-disposition"] == f'attachment; filename="Media_{name}"'
        assert response.headers["cache-control"] == "no-store"
        assert response.text == text


def test_a_log_lists_the_errors_up_to_a_limit(client: TestClient, scan_root: Path, tmp_path: Path,
                                              monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan_logs, "MAX_REPORTED_ERRORS", 10)
    use_worker(client, fake_worker(tmp_path, errors=30))
    done = scan(client, add_directory(client, scan_root))
    text = log_path(client, done["id"]).read_text(encoding="utf-8")
    assert sum(event.startswith("Error: ") for event in events(text)) == 10
    assert "  Errors: 30 (the first 10 are listed above)" in text.splitlines()


def test_the_worker_reports_every_error_as_it_happens(tmp_path: Path, capsys: pytest.CaptureFixture[str],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    def scan_with_errors(job, tree, errors, progress) -> None:  # noqa: ANN001
        tree.add(-1, "")
        for number in range(25):
            errors.add(f"folder-{number}", "Permission denied")

    monkeypatch.setattr(worker, "scan_native", scan_with_errors)
    job = tmp_path / "job.json"
    Job(engine="native", root=str(tmp_path), result_path=str(tmp_path / "result.jsonl")).dump(
        str(job)
    )
    previous = signal.getsignal(signal.SIGTERM)
    try:
        assert worker.main(["worker", str(job)]) == 0
    finally:
        signal.signal(signal.SIGTERM, previous)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line for line in lines if line["type"] == "scan_error"] == [
        {"type": "scan_error", "path": f"folder-{number}", "message": "Permission denied"}
        for number in range(25)
    ]
    # The sample of the scan's record stays at 20; every error is counted.
    assert lines[-1]["type"] == "done" and lines[-1]["error_count"] == 25
    assert len(lines[-1]["error_sample"]) == 20


def test_the_log_of_a_failed_scan_says_why(client: TestClient, scan_root: Path,
                                           tmp_path: Path) -> None:
    use_worker(client, fake_worker(tmp_path, fail=True))
    (scan_root / "a").mkdir()
    failed = scan(client, add_directory(client, scan_root / "a", low_priority=False))
    text = log_path(client, failed["id"]).read_text(encoding="utf-8")
    assert re.fullmatch(r"Failed after \d+ s: the scan engine failed", events(text)[-1])
    assert text.splitlines()[-1] == "  Reason: engine_failed: boom"

    crash = tmp_path / "crash.py"
    crash.write_text("import sys\nprint('first problem', file=sys.stderr)\n"
                     "print('second\\tproblem', file=sys.stderr)\nsys.exit(3)\n")
    use_worker(client, [sys.executable, str(crash)])
    (scan_root / "b").mkdir()
    crashed = scan(client, add_directory(client, scan_root / "b", low_priority=False))
    lines = log_path(client, crashed["id"]).read_text(encoding="utf-8").splitlines()
    assert lines[-4:] == [
        "  Reason: scan_failed: exit status 3: first problem | second\\x09problem",
        "  Last lines of the scan worker:",
        "    first problem",
        "    second\\x09problem",
    ]

    (scan_root / "gone").mkdir()
    directory = add_directory(client, scan_root / "gone")
    (scan_root / "gone").rmdir()
    vanished = scan(client, directory)
    text = log_path(client, vanished["id"]).read_text(encoding="utf-8")
    assert re.fullmatch(
        r"Failed after \d+ s: the folder does not exist \(it may not be mounted\)", events(text)[-1]
    )
    assert text.splitlines()[-1] == "  Reason: path_not_found"


def test_cancelled_scans(client: TestClient, scan_root: Path, tmp_path: Path) -> None:
    use_worker(client, fake_worker(tmp_path, seconds=30))
    (scan_root / "a").mkdir()
    (scan_root / "b").mkdir()
    first = add_directory(client, scan_root / "a")
    second = add_directory(client, scan_root / "b")
    running = client.post(f"/api/directories/{first}/scan", headers=WRITE).json()["scan_id"]
    queued = client.post(f"/api/directories/{second}/scan", headers=WRITE).json()["scan_id"]
    wait_for(lambda: client.get(f"/api/scans/{running}").json()["status"] == "running")
    # The log of a running scan downloads as far as it goes.
    wait_for(lambda: client.get(f"/api/scans/{running}").json()["has_log"])
    assert events(client.get(f"/api/scans/{running}/log").text) == ["Queued", "Started"]
    # A scan cancelled while queued never started: no log.
    client.post(f"/api/scans/{queued}/cancel", headers=WRITE)
    assert client.get(f"/api/scans/{queued}").json()["has_log"] is False
    missing = client.get(f"/api/scans/{queued}/log")
    assert missing.status_code == 404 and missing.json() == {"code": "scan_log_not_found"}
    client.post(f"/api/scans/{running}/cancel", headers=WRITE)
    wait_idle(client)
    text = log_path(client, running).read_text(encoding="utf-8")
    assert re.fullmatch(r"Cancelled after \d+ s", events(text)[-1])
    assert client.get("/api/scans/999999/log").json() == {"code": "scan_not_found"}


def test_scans_interrupted_by_a_restart_end_their_log(settings: Settings, scan_root: Path) -> None:
    migrate(settings.database_path)
    sessions = session_factory(create_db_engine(settings.database_path))
    with TestClient(create_app(settings)) as client:
        directory = add_directory(client, scan_root)
    folder = settings.logs_dir / "data"
    folder.mkdir(parents=True)
    (folder / "2026-11-20_0300.log").write_text("2026-11-20 03:00:00+00:00  Started\n")
    with transaction(sessions) as db:
        for name in ("2026-11-20_0300.log", "2026-11-20_0400.log"):
            db.add(Scan(directory_id=directory, status="running", trigger="manual",
                        engine="native", options=OPTIONS, queued_at=utcnow(), started_at=utcnow(),
                        error_count=0, error_sample=[], has_detail=False,
                        log_file=f"data/{name}"))
    with TestClient(create_app(settings)):
        pass
    lines = (folder / "2026-11-20_0300.log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert events(lines[1]) == ["Interrupted: DiskSprawl stopped during the scan"]
    # A log deleted meanwhile is never created again.
    assert not (folder / "2026-11-20_0400.log").exists()


@pytest.mark.parametrize(("name", "folder"), [
    ("Media", "Media"),
    ("Fotos de niños", "Fotos de niños"),
    ('Backups: 2024/25 "*final*" <old> | new?', "Backups_ 2024_25 __final__ _old_ _ new_"),
    ("..", "directory-7"),
    (" .hidden. ", "hidden"),
    ("tab\there\nnewline", "tab_here_newline"),
    ("ni\udcf1os", "ni_xf1os"),
    ("x" * 100, "x" * 80),
])
def test_log_folders_keep_only_safe_names(name: str, folder: str) -> None:
    assert folder_name(name, 7) == folder


def test_logs_that_start_in_the_same_minute(tmp_path: Path) -> None:
    logs = ScanLogs(tmp_path / "logs", ZoneInfo("Europe/Madrid"))
    started = datetime(2026, 7, 1, 1, 30, 59, tzinfo=UTC)
    about = About(1, 1, "Media", "/data/media", "scheduled", OPTIONS, started, started)
    names = []
    for _ in range(3):
        scan_log = logs.create(about)
        names.append(scan_log.relative)
        scan_log.close()
    # 01:30 UTC is 03:30 in Madrid in summer.
    assert names == ["Media/2026-07-01_0330.log", "Media/2026-07-01_0330_2.log",
                     "Media/2026-07-01_0330_3.log"]
    assert logs.path("../etc/passwd") is None and logs.path("Media/../../x.log") is None
    assert logs.path("Media/notes.txt") is None
    assert logs.path("Media/2026-07-01_0330.log") == tmp_path / "logs" / "Media" / "2026-07-01_0330.log"


def test_logs_expire(client: TestClient, scan_root: Path, tmp_path: Path) -> None:
    use_worker(client, fake_worker(tmp_path))
    (scan_root / "a").mkdir()
    directory = add_directory(client, scan_root / "a")
    old, recent = scan(client, directory), scan(client, directory)
    old_path, recent_path = log_path(client, old["id"]), log_path(client, recent["id"])
    age(old_path, 31)
    age(recent_path, 29)
    orphan = logs_dir(client) / "Gone" / "2025-01-01_0300.log"
    orphan.parent.mkdir()
    orphan.write_text("A log without a scan\n")
    age(orphan, 400)
    unrelated = logs_dir(client) / "a" / "notes.txt"
    unrelated.write_text("Not a log\n")
    age(unrelated, 400)
    # A running scan keeps its log, however old its last line.
    use_worker(client, fake_worker(tmp_path, seconds=30))
    running = client.post(f"/api/directories/{directory}/scan", headers=WRITE).json()["scan_id"]
    wait_for(lambda: client.get(f"/api/scans/{running}").json()["has_log"])
    age(log_path(client, running), 400)

    client.app.state.scheduler.sweep_logs()  # type: ignore[attr-defined]
    assert not old_path.exists() and recent_path.exists() and log_path(client, running).exists()
    assert not orphan.parent.exists() and unrelated.exists()
    assert client.get(f"/api/scans/{old['id']}").json()["has_log"] is False
    assert client.get(f"/api/scans/{old['id']}/log").json() == {"code": "scan_log_not_found"}
    assert client.get(f"/api/scans/{recent['id']}").json()["has_log"] is True
    client.post(f"/api/scans/{running}/cancel", headers=WRITE)
    wait_idle(client)


def test_a_sweep_the_database_refuses_deletes_nothing(client: TestClient, scan_root: Path,
                                                      tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """The scans forget their logs before the files go: a busy database leaves both for the next
    sweep, never a scan pointing to a deleted log."""
    use_worker(client, fake_worker(tmp_path))
    done = scan(client, add_directory(client, scan_root))
    path = log_path(client, done["id"])
    age(path, 31)

    def refused(*args: object) -> None:
        raise OperationalError("UPDATE scans", {}, Exception("database is locked"))

    with monkeypatch.context() as patch:
        patch.setattr(scanning, "update", refused)
        with pytest.raises(OperationalError):
            client.app.state.scanner.sweep_logs(30)  # type: ignore[attr-defined]
    assert path.exists() and client.get(f"/api/scans/{done['id']}").json()["has_log"] is True
    client.app.state.scanner.sweep_logs(30)  # type: ignore[attr-defined]
    assert not path.exists() and client.get(f"/api/scans/{done['id']}").json()["has_log"] is False


def test_a_scan_that_is_ending_keeps_its_log(client: TestClient, scan_root: Path, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Its record already says it completed, but its log has not got its end yet."""
    use_worker(client, fake_worker(tmp_path))
    ending, release = threading.Event(), threading.Event()
    end_log = Scanner._end_log

    def slow_end(self: Scanner, scan_id: int, scan_log: ScanLog) -> None:
        ending.set()
        release.wait(30)
        end_log(self, scan_id, scan_log)

    monkeypatch.setattr(Scanner, "_end_log", slow_end)
    directory = add_directory(client, scan_root)
    scan_id = client.post(f"/api/directories/{directory}/scan", headers=WRITE).json()["scan_id"]
    assert ending.wait(30)
    assert client.get(f"/api/scans/{scan_id}").json()["status"] == "completed"
    age(log_path(client, scan_id), 400)
    client.app.state.scanner.sweep_logs(1)  # type: ignore[attr-defined]
    release.set()
    wait_idle(client)
    assert events(log_path(client, scan_id).read_text(encoding="utf-8"))[-1].startswith(
        "Completed in "
    )


def test_shortening_the_retention_deletes_older_logs_at_once(client: TestClient, scan_root: Path,
                                                             tmp_path: Path) -> None:
    use_worker(client, fake_worker(tmp_path))
    done = scan(client, add_directory(client, scan_root))
    path = log_path(client, done["id"])
    age(path, 5)
    settings = {key: value for key, value in client.get("/api/settings").json().items()
                if key != "timezone"}
    client.put("/api/settings", json={**settings, "scan_log_days": 7}, headers=WRITE)
    assert path.exists()
    client.put("/api/settings", json={**settings, "scan_log_days": 3}, headers=WRITE)
    assert not path.exists() and not path.parent.exists()
    assert client.get(f"/api/scans/{done['id']}").json()["has_log"] is False


def test_deleting_a_directory_deletes_its_logs(client: TestClient, scan_root: Path,
                                               tmp_path: Path) -> None:
    use_worker(client, fake_worker(tmp_path))
    (scan_root / "a").mkdir()
    (scan_root / "b").mkdir()
    first = add_directory(client, scan_root / "a")
    second = add_directory(client, scan_root / "b")
    removed = log_path(client, scan(client, first)["id"])
    kept = log_path(client, scan(client, second)["id"])
    assert client.delete(f"/api/directories/{first}", headers=WRITE).status_code == 204
    assert not removed.exists() and not removed.parent.exists() and kept.exists()


def test_renaming_a_directory_keeps_its_earlier_logs(client: TestClient, scan_root: Path,
                                                     tmp_path: Path) -> None:
    use_worker(client, fake_worker(tmp_path))
    directory = add_directory(client, scan_root)
    before = scan(client, directory)
    client.put(f"/api/directories/{directory}", json={"name": "Fotos de niños",
                                                      "path": str(scan_root)}, headers=WRITE)
    after = scan(client, directory)
    assert log_file(client, before["id"]) == f"data/{stem(before['started_at'])}.log"
    name = stem(after["started_at"])
    assert log_file(client, after["id"]) == f"Fotos de niños/{name}.log"
    assert client.get(f"/api/scans/{before['id']}/log").status_code == 200
    response = client.get(f"/api/scans/{after['id']}/log")
    assert response.headers["content-disposition"] == (
        f"attachment; filename=\"Fotos de ni_os_{name}.log\";"
        f" filename*=UTF-8''Fotos%20de%20ni%C3%B1os_{name}.log"
    )
    assert "Directory: Fotos de niños" in response.text
