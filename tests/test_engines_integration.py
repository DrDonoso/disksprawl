"""The four engines give the same results on the same tree (each is skipped when its binary is
missing; CI installs them with scripts/install-engines.sh)."""

from pathlib import Path

import pytest
from conftest import add_directory, scan
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
