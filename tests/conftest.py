import json
import os
import sys
import time
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from push_helpers import PushService

from disksprawl import webpush
from disksprawl.app import create_app
from disksprawl.config import Settings
from disksprawl.db import Scan, transaction, utcnow
from disksprawl.notifications import Notifier
from disksprawl.scanning import import_entries
from disksprawl.scheduler import Scheduler

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures"
WRITE = {"Origin": "http://testserver", "X-DiskSprawl-Request": "1"}
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(autouse=True)
def paused_schedules(monkeypatch: pytest.MonkeyPatch) -> None:
    """No schedule fires on its own during a test: a directory scanned daily at 03:00 would get an
    extra scan in any test that runs at that moment. Tests call the scheduler's methods instead."""
    monkeypatch.setattr(Scheduler, "paused", True)


@pytest.fixture(autouse=True)
def paused_notifications(monkeypatch: pytest.MonkeyPatch) -> None:
    """No notification round runs on its own during a test: tests call Notifier.tick()."""
    monkeypatch.setattr(Notifier, "paused", True)


@pytest.fixture(autouse=True)
def push_service(monkeypatch: pytest.MonkeyPatch) -> PushService:
    """Stands in for every push service: nothing a test sends leaves the machine."""
    service = PushService()
    monkeypatch.setattr(webpush, "TRANSPORT", service)
    return service


def write_tree(root: Path, spec: dict[str, object]) -> None:
    """Creates files (int: size in bytes) and folders (dict) below `root`."""
    root.mkdir(parents=True, exist_ok=True)
    for name, value in spec.items():
        if isinstance(value, dict):
            write_tree(root / name, value)
        else:
            (root / name).write_bytes(b"x" * int(value))  # type: ignore[call-overload]


@pytest.fixture
def scan_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    return root


@pytest.fixture
def settings(tmp_path: Path, scan_root: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "state",
        scan_root=scan_root,
        frontend_dir=tmp_path / "dist",
        # The server's time zone, whatever TZ says on the machine running the tests.
        tz="Etc/UTC",
        _env_file=None,  # type: ignore[call-arg]
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def fake_worker(tmp_path: Path, *, seconds: float = 0.0, fail: bool = False) -> list[str]:
    """A scripted scan worker: progress for `seconds`, then a two-folder result (or a failure)."""
    script = tmp_path / "fake_worker.py"
    script.write_text(
        f"""
import json, sys, time
job = json.load(open(sys.argv[1]))
end = time.monotonic() + {seconds}
done = 0
while time.monotonic() < end:
    done += 1
    print(json.dumps({{"type": "progress", "phase": "scanning", "live": True, "dirs": done,
                      "files": done * 10, "bytes": done * 1000, "current": f"folder-{{done}}"}}),
          flush=True)
    time.sleep(0.05)
if {fail}:
    print(json.dumps({{"type": "error", "code": "engine_failed", "detail": "boom"}}), flush=True)
    sys.exit(1)
with open(job["result_path"], "w") as handle:
    handle.write(json.dumps([0, -1, "", 3000, 3, 1]) + "\\n")
    handle.write(json.dumps([1, 0, "child", 2000, 2, 0]) + "\\n")
print(json.dumps({{"type": "done", "total_bytes": 3000, "file_count": 3, "dir_count": 1,
                  "entries": 2, "error_count": 0, "error_sample": []}}), flush=True)
"""
    )
    return [sys.executable, str(script)]


def wait_idle(client: TestClient, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not client.get("/api/queue").json()["items"]:
            return
        time.sleep(0.05)
    raise AssertionError("The scan queue did not empty in time")


def add_directory(client: TestClient, path: Path, **options: object) -> int:
    response = client.post(
        "/api/directories",
        json={"name": path.name or "root", "path": str(path), **options},
        headers=WRITE,
    )
    assert response.status_code == 201, response.text
    return int(response.json()["directory"]["id"])


def scan(client: TestClient, directory_id: int) -> dict[str, object]:
    """Runs a scan to the end and returns it."""
    response = client.post(f"/api/directories/{directory_id}/scan", headers=WRITE)
    assert response.status_code == 202, response.text
    scan_id = response.json()["scan_id"]
    wait_idle(client)
    result: dict[str, object] = client.get(f"/api/scans/{scan_id}").json()
    return result


OPTIONS = {"engine": "native", "threads": 2, "pace": "max", "low_priority": False,
           "measure": "apparent", "one_filesystem": True, "excludes": []}


def running_as_root() -> bool:
    return os.geteuid() == 0


def load_fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def result_rows(path: str) -> list[list[object]]:
    with open(path, encoding="utf-8") as handle:
        return [json.loads(line) for line in handle]


def record(client: TestClient, directory: int, sizes: dict[str, int], tmp_path: Path,
           days_ago: float = 0) -> int:
    """Stores a completed scan whose folders have these sizes ('' is the root)."""
    ordered = sorted(sizes, key=lambda path: (path.count("/") if path else -1, path))
    index = {path: number for number, path in enumerate(ordered)}
    result = tmp_path / f"result-{len(list(tmp_path.glob('result-*')))}.jsonl"
    with open(result, "w", encoding="utf-8") as handle:
        for path in ordered:
            parent = path.rsplit("/", 1)[0] if "/" in path else ""
            handle.write(json.dumps([
                index[path], index[parent] if path else -1, path.rsplit("/", 1)[-1],
                sizes[path], 1, path.count("/"),
            ]) + "\n")
    moment = utcnow() - timedelta(days=days_ago)
    with transaction(client.app.state.sessions) as db:  # type: ignore[attr-defined]
        scan = Scan(directory_id=directory, status="completed", trigger="manual", engine="native",
                    options=OPTIONS, queued_at=moment, started_at=moment,
                    finished_at=moment, total_bytes=sizes[""], dir_count=len(sizes) - 1,
                    error_count=0, error_sample=[], has_detail=True)
        db.add(scan)
        db.flush()
        import_entries(db, scan.id, directory, result)
        return scan.id
