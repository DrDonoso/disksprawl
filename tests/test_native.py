"""The native engine and the folder tree it builds."""

import io
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest
from conftest import running_as_root, write_tree

from disksprawl.engines.common import (
    ErrorLog,
    Excluder,
    Job,
    Progress,
    Tree,
    display_name,
    read_result,
)
from disksprawl.engines.native import rest_ratio, scan_native


def run(root: Path, **options: object) -> tuple[dict[str, tuple[int, int | None, int]], ErrorLog]:
    job = Job(engine="native", root=str(root), result_path="unused", min_entry_bytes=0,
              **options)  # type: ignore[arg-type]
    tree, errors = Tree(), ErrorLog()
    scan_native(job, tree, errors, Progress(io.StringIO()), sleep=lambda seconds: None)
    result = tree.finalize(0)
    names: dict[int, str] = {}
    folders = {}
    for index, parent, name, size, files, dirs in result.entries:
        path = f"{names[parent]}/{name}".lstrip("/") if parent >= 0 else ""
        names[index] = path
        folders[path] = (size, files, dirs)
    return folders, errors


def test_sizes_files_and_folders(tmp_path: Path) -> None:
    write_tree(tmp_path, {"a": {"one": 100, "b": {"two": 20}}, "three": 3, "empty": {}})
    folders, errors = run(tmp_path, threads=3)
    assert folders[""] == (123, 3, 3)
    assert folders["a"] == (120, 2, 1)
    assert folders["a/b"] == (20, 1, 0)
    assert folders["empty"] == (0, 0, 0)
    assert errors.count == 0


def test_hard_links_count_once_in_the_first_folder_by_path(tmp_path: Path) -> None:
    write_tree(tmp_path, {"b": {"file": 500}, "a": {}})
    os.link(tmp_path / "b" / "file", tmp_path / "a" / "link")
    for threads in (1, 4):
        folders, _ = run(tmp_path, threads=threads)
        assert folders[""] == (500, 2, 2)
        assert folders["a"][0] == 500
        assert folders["b"][0] == 0


def test_symbolic_links_are_not_followed(tmp_path: Path) -> None:
    write_tree(tmp_path / "outside", {"big": 10_000})
    write_tree(tmp_path / "root", {"small": 10})
    (tmp_path / "root" / "to-outside").symlink_to(tmp_path / "outside")
    folders, _ = run(tmp_path / "root")
    link_size = len(str(tmp_path / "outside"))
    assert folders[""] == (10 + link_size, 2, 0)


def test_exclusions_match_names(tmp_path: Path) -> None:
    write_tree(tmp_path, {
        ".snapshot": {"copy": 1000},
        "keep": {"a.tmp": 5, "b.txt": 7, "#recycle": {"x": 9}},
    })
    folders, _ = run(tmp_path, excludes=[".snapshot", "*.tmp"])
    assert ".snapshot" not in folders
    assert folders[""] == (16, 2, 2)


def test_excluder_wildcards() -> None:
    excluded = Excluder(["#snap*", "?.bak", "a b"])
    assert excluded("#snapshot") and excluded("x.bak") and excluded("a b")
    assert not excluded("xy.bak") and not excluded("snap") and not excluded("a b c")
    assert not Excluder([])("anything")


def test_disk_usage_counts_folder_blocks(tmp_path: Path) -> None:
    write_tree(tmp_path, {"a": {"f": 10}})
    folders, _ = run(tmp_path, measure="disk")
    expected = sum(
        os.lstat(path).st_blocks * 512 for path in (tmp_path, tmp_path / "a", tmp_path / "a" / "f")
    )
    assert folders[""][0] == expected


@pytest.mark.skipif(running_as_root(), reason="root reads unreadable folders")
def test_permission_denied_is_reported(tmp_path: Path) -> None:
    write_tree(tmp_path, {"open": {"f": 10}, "locked": {"secret": 99}})
    (tmp_path / "locked").chmod(0)
    try:
        folders, errors = run(tmp_path)
    finally:
        (tmp_path / "locked").chmod(0o755)
    assert folders[""] == (10, 1, 2)
    assert errors.count == 1
    assert errors.sample[0]["path"] == "locked"


@dataclass
class FakeStat:
    st_dev: int
    st_size: int = 0
    st_blocks: int = 0
    st_nlink: int = 1
    st_ino: int = 0


@dataclass
class FakeEntry:
    name: str
    path: str
    folder: bool
    device: int

    def is_dir(self, follow_symlinks: bool = True) -> bool:
        return self.folder

    def stat(self, follow_symlinks: bool = True) -> FakeStat:
        return FakeStat(self.device, 0 if self.folder else 10)


class FakeListing:
    def __init__(self, entries: list[FakeEntry]):
        self.entries = iter(entries)

    def __enter__(self) -> "FakeListing":
        return self

    def __exit__(self, *args: object) -> None:
        pass

    def __iter__(self) -> "FakeListing":
        return self

    def __next__(self) -> FakeEntry:
        return next(self.entries)


def test_one_filesystem_skips_other_devices(tmp_path: Path) -> None:
    root = str(tmp_path)
    device = os.lstat(root).st_dev
    listings = {
        root: [
            FakeEntry("local", f"{root}/local", True, device),
            FakeEntry("mounted", f"{root}/mounted", True, device + 1),
            FakeEntry("file", f"{root}/file", False, device),
        ],
        f"{root}/local": [FakeEntry("inner", f"{root}/local/inner", False, device)],
        f"{root}/mounted": [FakeEntry("remote", f"{root}/mounted/remote", False, device + 1)],
    }
    for one_filesystem, expected in ((True, (20, 2, 1)), (False, (30, 3, 2))):
        job = Job(engine="native", root=root, result_path="unused", min_entry_bytes=0,
                  one_filesystem=one_filesystem)
        tree = Tree()
        scan_native(job, tree, ErrorLog(), Progress(io.StringIO()),
                    scandir=lambda path: FakeListing(listings[path]))
        result = tree.finalize(0)
        assert (result.total_bytes, result.file_count, result.dir_count) == expected


def test_pace_rests_its_share_of_the_time(tmp_path: Path) -> None:
    write_tree(tmp_path, {f"folder{n}": {"f": 1} for n in range(5)})
    for pace, ratio in (("max", 0.0), ("balanced", 1 / 3), ("gentle", 1.0)):
        assert rest_ratio(pace) == pytest.approx(ratio)
        ticks = iter(range(0, 1000, 2))  # every folder takes 2 "seconds" of work
        rests: list[float] = []
        job = Job(engine="native", root=str(tmp_path), result_path="unused", pace=pace)
        scan_native(job, Tree(), ErrorLog(), Progress(io.StringIO()),
                    sleep=rests.append, clock=lambda ticks=ticks: float(next(ticks)))
        assert rests == ([pytest.approx(2 * ratio)] * 6 if ratio else [])


def test_finalize_keeps_large_folders_with_their_parents() -> None:
    tree = Tree()
    root = tree.add(-1, "")
    big = tree.add(root, "big")
    small = tree.add(root, "small")
    deep = tree.add(big, "deep")
    tree.add_file(deep, 5000)
    tree.add_file(small, 10)
    tree.add_file(root, 1)
    result = tree.finalize(1000)
    assert [(entry[2], entry[3]) for entry in result.entries] == [
        ("", 5011), ("big", 5000), ("deep", 5000)
    ]
    assert (result.total_bytes, result.file_count, result.dir_count) == (5011, 3, 3)


def test_names_that_are_not_utf8(tmp_path: Path) -> None:
    """Linux names are bytes, not always UTF-8 (old Latin-1 names): the tree, the errors, the
    progress and the result file get them as valid text with `\\xNN` escapes."""
    root = os.fsencode(tmp_path / "tree")
    music = os.path.join(root, b"M\xfasica")
    os.makedirs(os.path.join(music, b"Cl\xe1sica"))
    with open(os.path.join(music, b"Cl\xe1sica", b"song\xff.flac"), "wb") as handle:
        handle.truncate(3 * 2**20)
    os.makedirs(os.path.join(root, b"Fotos ni\xf1os"))

    def scandir(path: str):  # noqa: ANN202 - os.scandir's iterator
        if os.fsencode(path).endswith(b"Fotos ni\xf1os"):
            raise PermissionError(13, "Permission denied")
        return os.scandir(path)

    job = Job(engine="native", root=str(tmp_path / "tree"),
              result_path=str(tmp_path / "result.jsonl"), min_entry_bytes=2**20)
    out = io.StringIO()
    progress = Progress(out)
    tree, errors = Tree(), ErrorLog()
    scan_native(job, tree, errors, progress, sleep=lambda seconds: None, scandir=scandir)
    progress.stop()
    result = tree.finalize(job.min_entry_bytes)
    result.write(job.result_path)
    stored = [(name, size) for _, _, name, size, _, _ in read_result(job.result_path)]
    assert stored == [("", 3 * 2**20), ("M\\xfasica", 3 * 2**20), ("Cl\\xe1sica", 3 * 2**20)]
    assert errors.sample == [{"path": "Fotos ni\\xf1os", "message": "Permission denied"}]
    # Progress lines are ASCII JSON, whatever the names and the locale.
    lines = out.getvalue().splitlines()
    assert lines and all(line.isascii() for line in lines)
    assert json.loads(lines[-1])["current"] in ("", "M\\xfasica", "M\\xfasica/Cl\\xe1sica",
                                                "Fotos ni\\xf1os")


def test_display_name() -> None:
    assert display_name(b"M\xfasica") == display_name("M\udcfasica") == "M\\xfasica"
    assert display_name(b"caf\xc3\xa9") == display_name("café") == "café"
    # Clean text is never changed again; a surrogate that stands for no byte is escaped too.
    assert display_name("M\\xfasica") == "M\\xfasica"
    assert display_name("a\ud800") == "a\\xed\\xa0\\x80"


def test_names_that_look_the_same_stay_apart() -> None:
    """Two folders whose names differ only in bytes that are not UTF-8 can look the same (gdu
    writes each as U+FFFD): the second one gets a number, so both are stored."""
    tree = Tree()
    root = tree.add(-1, "")
    for name in ("M\ufffdsica", "M\ufffdsica", "M\ufffdsica (2)"):
        tree.add_file(tree.add(root, name), 10)
    names = [entry[2] for entry in tree.finalize(0).entries[1:]]
    assert names == ["M\ufffdsica", "M\ufffdsica (2)", "M\ufffdsica (2) (2)"]
