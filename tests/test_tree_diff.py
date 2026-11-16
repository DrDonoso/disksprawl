"""Folder trees, changes between scans and retention, on scans stored directly."""

from pathlib import Path

from conftest import add_directory, record
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from disksprawl import retention
from disksprawl.db import PathNode, Scan, ScanEntry

BEFORE = {"": 1000, "media": 600, "media/movies": 500, "old": 300, "docs": 50}
AFTER = {"": 1300, "media": 1100, "media/movies": 500, "media/series": 550, "docs": 50}


def test_tree_lists_children_by_size(client: TestClient, scan_root: Path, tmp_path: Path):
    directory = add_directory(client, scan_root)
    record(client, directory, BEFORE, tmp_path, days_ago=1)
    latest = record(client, directory, AFTER, tmp_path)
    tree = client.get(f"/api/directories/{directory}/tree").json()
    assert tree["scan"]["id"] == latest and tree["path"] == "" and tree["size"] == 1300
    assert [(c["name"], c["size"], c["has_children"]) for c in tree["children"]] == [
        ("media", 1100, True), ("docs", 50, False)
    ]
    assert tree["other_size"] == 150
    media = client.get(f"/api/directories/{directory}/tree", params={"path": "media"}).json()
    assert [c["path"] for c in media["children"]] == ["media/series", "media/movies"]
    assert client.get(f"/api/directories/{directory}/tree",
                      params={"path": "nope"}).json()["code"] == "path_not_found"
    assert client.get(f"/api/directories/{directory}/tree",
                      params={"path": "media/../.."}).status_code == 404


def test_diff_shows_the_union_of_both_scans(client: TestClient, scan_root: Path, tmp_path: Path):
    directory = add_directory(client, scan_root)
    first = record(client, directory, BEFORE, tmp_path, days_ago=1)
    second = record(client, directory, AFTER, tmp_path)
    diff = client.get(f"/api/directories/{directory}/diff").json()
    assert (diff["from_scan"]["id"], diff["to_scan"]["id"]) == (first, second)
    assert (diff["before"], diff["after"]) == (1000, 1300)
    assert [(c["name"], c["before"], c["after"]) for c in diff["children"]] == [
        ("media", 600, 1100), ("old", 300, None), ("docs", 50, 50)
    ]
    assert (diff["other_before"], diff["other_after"]) == (50, 150)
    # The new series explain the growth of media: the more specific folder is listed.
    assert [(c["path"], c["before"], c["after"]) for c in diff["biggest"]] == [
        ("media/series", None, 550), ("old", 300, None)
    ]
    explicit = client.get(f"/api/directories/{directory}/diff",
                          params={"from": second, "to": first, "path": "media"}).json()
    assert [(c["name"], c["before"], c["after"]) for c in explicit["children"]] == [
        ("series", 550, None), ("movies", 500, 500)
    ]


def test_diff_needs_two_scans(client: TestClient, scan_root: Path, tmp_path: Path) -> None:
    directory = add_directory(client, scan_root)
    assert client.get(f"/api/directories/{directory}/tree").json()["code"] == "no_scan_yet"
    record(client, directory, BEFORE, tmp_path)
    assert client.get(f"/api/directories/{directory}/diff").json()["code"] == "no_previous_scan"


def test_retention_keeps_the_latest_detailed_scans(client: TestClient, scan_root: Path,
                                                   tmp_path: Path) -> None:
    directory = add_directory(client, scan_root)
    old = record(client, directory, {"": 10, "gone": 5}, tmp_path, days_ago=3)
    middle = record(client, directory, BEFORE, tmp_path, days_ago=2)
    latest = record(client, directory, AFTER, tmp_path, days_ago=1)
    sessions = client.app.state.sessions  # type: ignore[attr-defined]
    # Batches of two rows: every deletion takes several transactions.
    retention.prune(sessions, directory, 2, batch=2)
    with sessions() as db:
        detail = dict(db.execute(select(Scan.id, Scan.has_detail)).all())
        assert detail == {old: False, middle: True, latest: True}
        assert db.scalar(select(func.count()).where(ScanEntry.scan_id == old)) == 0
        names = set(db.scalars(select(PathNode.name)))
        assert "gone" not in names and "old" in names
    scans = client.get(f"/api/directories/{directory}/scans").json()["items"]
    assert [s["total_bytes"] for s in scans] == [1300, 1000, 10]
    assert client.get(f"/api/directories/{directory}/tree",
                      params={"scan": old}).json()["code"] == "scan_without_detail"


def test_paths_keep_their_ids_across_scans(client: TestClient, scan_root: Path, tmp_path: Path):
    directory = add_directory(client, scan_root)
    record(client, directory, BEFORE, tmp_path, days_ago=1)
    with client.app.state.sessions() as db:  # type: ignore[attr-defined]
        before = dict(db.execute(select(PathNode.name, PathNode.id)).all())
    record(client, directory, AFTER, tmp_path)
    with client.app.state.sessions() as db:  # type: ignore[attr-defined]
        after = dict(db.execute(select(PathNode.name, PathNode.id)).all())
    assert after["media"] == before["media"] and after["movies"] == before["movies"]
    assert "series" in after
