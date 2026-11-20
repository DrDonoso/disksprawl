"""Directories, settings, schedules and the folder picker through the API."""

import os
from pathlib import Path

import pytest
from conftest import WRITE, add_directory
from fastapi.testclient import TestClient

from disksprawl.engines import EngineInfo


def test_new_directories_get_the_defaults(client: TestClient, scan_root: Path) -> None:
    created = client.post("/api/directories", json={"name": "  My   media ", "path": f"{scan_root}/"},
                          headers=WRITE)
    assert created.status_code == 201
    body = created.json()
    directory = body["directory"]
    assert directory["name"] == "My media" and directory["path"] == str(scan_root)
    assert {key: directory[key] for key in (
        "cron", "engine", "threads", "pace", "low_priority", "measure", "one_filesystem", "excludes"
    )} == {
        "cron": "0 3 * * *", "engine": "native", "threads": 2, "pace": "balanced",
        "low_priority": True, "measure": "apparent", "one_filesystem": True,
        "excludes": ["#snapshot", ".snapshot", ".zfs"],
    }
    assert body["next_run"] is not None and body["last_scan"] is None
    assert body["capacity"]["total"] > 0 and body["capacity"]["shared_with"] == []


def test_validation(client: TestClient, scan_root: Path, tmp_path: Path) -> None:
    def create(**fields: object) -> dict[str, object]:
        response = client.post("/api/directories",
                               json={"name": "x", "path": str(scan_root), **fields}, headers=WRITE)
        assert response.status_code in (409, 422), response.text
        return response.json()  # type: ignore[no-any-return]

    (scan_root / "file").write_text("x")
    (scan_root / "escape").symlink_to(tmp_path)
    assert create(path=str(tmp_path))["fields"] == {"path": "path_outside_root"}
    assert create(path=str(scan_root / "escape"))["fields"] == {"path": "path_outside_root"}
    assert create(path=str(scan_root / "missing"))["fields"] == {"path": "path_not_found"}
    assert create(path=str(scan_root / "file"))["fields"] == {"path": "path_not_directory"}
    assert create(path="relative")["fields"] == {"path": "path_invalid"}
    assert create(cron="61 * * * *")["fields"] == {"cron": "invalid_cron"}
    assert create(cron="* * *")["fields"] == {"cron": "invalid_cron"}
    assert create(excludes=["a/b"])["fields"] == {"excludes": "invalid_exclude"}
    assert create(excludes=["[x]"])["fields"] == {"excludes": "invalid_exclude"}
    assert create(threads=17)["fields"] == {"threads": "invalid"}
    assert create(name="   ")["fields"] == {"name": "name_required"}
    assert create(engine="rsync")["fields"] == {"engine": "invalid"}
    add_directory(client, scan_root)
    assert create()["fields"] == {"path": "duplicate_path"}


def test_engine_limits(client: TestClient, scan_root: Path) -> None:
    engines = client.app.state.engines  # type: ignore[attr-defined]
    engines["gdu"] = EngineInfo("gdu", True, "test", "/bin/false", 8, "none")
    engines["du"] = EngineInfo("du", True, "test", "/bin/false", 1, "folders")
    engines["ncdu"] = EngineInfo("ncdu", False, None, None, 16, "single_thread")
    (scan_root / "a").mkdir()
    response = client.post("/api/directories", json={
        "name": "a", "path": str(scan_root / "a"), "engine": "gdu", "threads": 9}, headers=WRITE)
    assert response.json()["fields"] == {"threads": "threads_out_of_range"}
    response = client.post("/api/directories", json={
        "name": "a", "path": str(scan_root / "a"), "engine": "du", "threads": 6}, headers=WRITE)
    assert response.json()["directory"]["threads"] == 1
    response = client.put(f"/api/directories/{response.json()['directory']['id']}", json={
        "name": "a", "path": str(scan_root / "a"), "engine": "ncdu"}, headers=WRITE)
    assert response.json()["fields"] == {"engine": "engine_unavailable"}


def test_update_reschedules_and_delete_keeps_files(client: TestClient, scan_root: Path) -> None:
    (scan_root / "keep.txt").write_text("precious")
    directory = add_directory(client, scan_root)
    updated = client.put(f"/api/directories/{directory}", json={
        "name": "Renamed", "path": str(scan_root), "cron": None, "enabled": True,
    }, headers=WRITE).json()
    assert updated["directory"]["name"] == "Renamed" and updated["next_run"] is None
    hourly = client.put(f"/api/directories/{directory}", json={
        "name": "Renamed", "path": str(scan_root), "cron": "  0  *  * * * ",
    }, headers=WRITE).json()
    assert hourly["directory"]["cron"] == "0 * * * *" and hourly["next_run"] is not None
    disabled = client.put(f"/api/directories/{directory}", json={
        "name": "Renamed", "path": str(scan_root), "cron": "0 * * * *", "enabled": False,
    }, headers=WRITE).json()
    assert disabled["next_run"] is None
    assert client.delete(f"/api/directories/{directory}", headers=WRITE).status_code == 204
    assert client.get(f"/api/directories/{directory}/overview").status_code == 404
    assert (scan_root / "keep.txt").read_text() == "precious"


def test_order_and_shared_volumes(client: TestClient, scan_root: Path) -> None:
    for name in ("a", "b", "c"):
        (scan_root / name).mkdir()
    ids = [add_directory(client, scan_root / name) for name in ("a", "b", "c")]
    response = client.put("/api/directories/order", json={"ids": [ids[2], ids[0], ids[1]]},
                          headers=WRITE)
    items = response.json()["items"]
    assert [item["directory"]["name"] for item in items] == ["c", "a", "b"]
    assert {ref["name"] for ref in items[0]["capacity"]["shared_with"]} == {"a", "b"}
    bad = client.put("/api/directories/order", json={"ids": ids[:2]}, headers=WRITE)
    assert bad.json()["code"] == "invalid_order"


def test_settings(client: TestClient) -> None:
    current = client.get("/api/settings").json()
    assert current == {"capacity_interval_minutes": 15, "keep_scans": 30, "min_entry_mib": 1,
                       "warn_percent": 80, "critical_percent": 90, "scan_log_days": 30,
                       "timezone": "Etc/UTC"}
    changed = {**current, "keep_scans": 5, "capacity_interval_minutes": 60, "scan_log_days": 3650}
    del changed["timezone"]
    assert client.put("/api/settings", json=changed, headers=WRITE).json()["keep_scans"] == 5
    assert client.get("/api/settings").json()["capacity_interval_minutes"] == 60
    assert client.get("/api/settings").json()["scan_log_days"] == 3650
    wrong = {**changed, "warn_percent": 95, "critical_percent": 90}
    assert client.put("/api/settings", json=wrong, headers=WRITE).json()["code"] == (
        "thresholds_out_of_order"
    )
    assert client.put("/api/settings", json={**changed, "keep_scans": 0},
                      headers=WRITE).json()["fields"] == {"keep_scans": "invalid"}
    for days in (0, 3651):
        assert client.put("/api/settings", json={**changed, "scan_log_days": days},
                          headers=WRITE).json()["fields"] == {"scan_log_days": "invalid"}


def test_cron_preview(client: TestClient) -> None:
    preview = client.get("/api/cron/preview", params={"expression": "30 4 * * 1"}).json()
    assert preview["expression"] == "30 4 * * 1" and len(preview["next_runs"]) == 3
    assert client.get("/api/cron/preview", params={"expression": "nope"}).json()["code"] == (
        "invalid_cron"
    )


def test_browse_stays_inside_the_scan_root(client: TestClient, scan_root: Path, tmp_path: Path):
    for name in ("Media", "backups", ".hidden"):
        (scan_root / name).mkdir()
    (scan_root / "Media" / "Movies").mkdir()
    (scan_root / "file.txt").write_text("x")
    (scan_root / "outside").symlink_to(tmp_path)
    (scan_root / "inside").symlink_to(scan_root / "Media")
    root = client.get("/api/browse").json()
    assert root["path"] == str(scan_root) and root["parent"] is None
    assert [entry["name"] for entry in root["entries"]] == [".hidden", "backups", "inside", "Media"]
    media = client.get("/api/browse", params={"path": f"{scan_root}/Media"}).json()
    assert media["parent"] == str(scan_root)
    assert media["entries"] == [{"name": "Movies", "path": f"{scan_root}/Media/Movies"}]
    for path in (str(tmp_path), f"{scan_root}/../", f"{scan_root}/outside", "/etc"):
        assert client.get("/api/browse", params={"path": path}).status_code == 422, path


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads unreadable folders")
def test_browse_unreadable_folder(client: TestClient, scan_root: Path) -> None:
    (scan_root / "locked").mkdir()
    (scan_root / "locked").chmod(0)
    try:
        response = client.get("/api/browse", params={"path": f"{scan_root}/locked"})
    finally:
        (scan_root / "locked").chmod(0o755)
    assert response.json()["code"] == "path_unreadable"


def test_version_engines_and_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/version").json() == {"version": "development", "build_date": ""}
    engines = {item["id"]: item for item in client.get("/api/engines").json()["items"]}
    assert list(engines) == ["native", "du", "ncdu", "gdu"]
    assert engines["native"]["available"] and engines["native"]["max_threads"] == 16
    assert client.get("/api/nothing").json()["code"] == "not_found"
