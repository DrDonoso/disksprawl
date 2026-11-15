"""The four engines give the same results on the same tree (each is skipped when its binary is
missing; CI installs them with scripts/install-engines.sh)."""

import os
from pathlib import Path

import pytest
from conftest import WRITE, add_directory, running_as_root, scan
from fastapi.testclient import TestClient

from disksprawl.engines import detect_engines

ENGINES = detect_engines()


@pytest.fixture
def sample(scan_root: Path) -> Path:
    import make_sample_tree

    root = scan_root / "sample"
    make_sample_tree.create(root, seed=7)
    return root


def results(client: TestClient, sample: Path, measure: str) -> dict[str, tuple[object, ...]]:
    directory = add_directory(client, sample, engine="native", measure=measure, pace="max")
    found: dict[str, tuple[object, ...]] = {}
    for engine, info in ENGINES.items():
        if not info.available:
            continue
        threads = min(4, info.max_threads)
        client.put(f"/api/directories/{directory}", json={
            "name": "sample", "path": str(sample), "engine": engine, "measure": measure,
            "threads": threads, "pace": "max",
        }, headers={"Origin": "http://testserver", "X-DiskSprawl-Request": "1"})
        done = scan(client, directory)
        assert done["status"] == "completed", done
        assert done["error_count"] == 0
        found[engine] = (done["total_bytes"], done["dir_count"], done["file_count"])
    return found


@pytest.mark.parametrize("measure", ["apparent", "disk"])
def test_every_engine_gives_the_same_totals(client: TestClient, sample: Path, measure: str):
    found = results(client, sample, measure)
    native = found.pop("native")
    if not found:
        pytest.skip("no external engine installed")
    for engine, (total, dirs, files) in found.items():
        assert total == native[0], engine
        assert dirs == native[1], engine
        assert files == (None if engine == "du" else native[2]), engine
    if measure == "apparent":
        # Large sparse files: the apparent size is far beyond the space on disk.
        assert int(native[0]) > 500 * 2**30  # type: ignore[call-overload]


def test_the_sample_tree_changes(client: TestClient, sample: Path) -> None:
    import make_sample_tree

    directory = add_directory(client, sample, pace="max")
    first = scan(client, directory)
    make_sample_tree.mutate(sample, 1)
    second = scan(client, directory)
    assert first["total_bytes"] != second["total_bytes"]
    diff = client.get(f"/api/directories/{directory}/diff").json()
    assert any(change["before"] is None for change in diff["biggest"])
    assert any(child["name"] == "#recycle" and child["after"] is None
               for child in diff["children"])


def test_names_that_are_not_utf8(client: TestClient, scan_root: Path) -> None:
    """A 3 MiB folder and an unreadable one with Latin-1 names, which are not UTF-8: every engine
    completes the scan, stores the folder and reports the error (gdu writes U+FFFD for each byte
    that is not UTF-8, the others an `\\xNN` escape)."""
    root = scan_root / "latin1"
    raw = os.fsencode(root)
    os.makedirs(os.path.join(raw, b"M\xfasica"))
    with open(os.path.join(raw, b"M\xfasica", b"canci\xf3n.flac"), "wb") as handle:
        handle.truncate(3 * 2**20)
    locked = os.path.join(raw, b"Fotos ni\xf1os")
    os.makedirs(locked)
    with open(os.path.join(locked, b"foto.jpg"), "wb") as handle:
        handle.write(b"x" * 100)
    os.chmod(locked, 0)
    directory = add_directory(client, root, pace="max")
    try:
        for engine, info in ENGINES.items():
            if not info.available:
                continue
            client.put(f"/api/directories/{directory}", json={
                "name": "latin1", "path": str(root), "engine": engine, "measure": "apparent",
                "threads": min(2, info.max_threads), "pace": "max",
            }, headers=WRITE)
            done = scan(client, directory)
            assert done["status"] == "completed", (engine, done)
            replaced = engine == "gdu"
            tree = client.get(f"/api/directories/{directory}/tree").json()
            assert [(child["name"], child["size"]) for child in tree["children"]] == [
                ("M\ufffdsica" if replaced else "M\\xfasica", 3 * 2**20)
            ], engine
            if running_as_root():
                assert done["error_count"] == 0, engine
            else:
                assert done["error_count"] == 1, (engine, done)
                assert done["error_sample"][0]["path"] == (  # type: ignore[index]
                    "Fotos ni\ufffdos" if replaced else "Fotos ni\\xf1os"
                ), engine
        progress = client.get("/api/queue").json()
        assert progress == {"items": []}
    finally:
        os.chmod(locked, 0o755)


def test_browse_leaves_out_names_that_are_not_utf8(client: TestClient, scan_root: Path) -> None:
    """No request can name a folder that is not UTF-8, so the folder picker does not offer it."""
    os.makedirs(os.path.join(os.fsencode(scan_root), b"caf\xe9"))
    (scan_root / "café").mkdir()
    response = client.get("/api/browse", params={"path": str(scan_root)})
    assert response.status_code == 200, response.text
    assert [entry["name"] for entry in response.json()["entries"]] == ["café"]
