"""The engines' parsers, on outputs recorded from the real binaries (tests/fixtures).

The recorded tree has names with spaces, a newline and accents, a hard link in two folders, a
symbolic link, a sparse file, an empty folder, an unreadable folder and an excluded `.snapshot`.
"""

import io

from conftest import load_fixture

from disksprawl.engines.common import ErrorLog, Job, Progress, Tree
from disksprawl.engines.du import du_command, du_error, parse_du, shell_unquote
from disksprawl.engines.gdu import gdu_command, gdu_errors, ignore_patterns, parse_gdu
from disksprawl.engines.ncdu import TerminalProgress, ncdu_command, parse_ncdu

ROOT = "/srv/tree"
APPARENT_TOTAL = 1000 + 5000 + 777 + 123 + 2048 + 3 + 1048576  # files only, the hard link once
DISK_TOTAL = 24576 + 7 * 4096  # the files' blocks plus the seven folders' own blocks
FOLDERS = 6
FILES = 8


def job(measure: str = "apparent", **options: object) -> Job:
    values: dict[str, object] = {"excludes": [".snapshot"], **options}
    return Job(engine="ncdu", root=ROOT, result_path="unused", measure=measure,
               min_entry_bytes=0, **values)  # type: ignore[arg-type]


def paths(tree: Tree) -> dict[str, tuple[int, int | None, int]]:
    result = tree.finalize(0)
    names: dict[int, str] = {}
    out = {}
    for index, parent, name, size, files, dirs in result.entries:
        path = f"{names[parent]}/{name}".lstrip("/") if parent >= 0 else ""
        names[index] = path
        out[path] = (size, files, dirs)
    return out


def quiet() -> Progress:
    return Progress(io.StringIO())


def test_ncdu_export_counts_like_du() -> None:
    for measure, total in (("apparent", APPARENT_TOTAL), ("disk", DISK_TOTAL)):
        tree, errors = Tree(), ErrorLog()
        finished = parse_ncdu(io.BytesIO(load_fixture("ncdu.json")), job(measure), tree, errors,
                              quiet())
        assert finished
        folders = paths(tree)
        assert folders[""] == (total, FILES, FOLDERS)
        assert set(folders) == {"", "a b", "a b/c", "uni-ñandú", "nl\nline", "locked", "empty"}
        assert errors.count == 1 and errors.sample[0]["path"] == "locked"
    assert paths(tree)["a b/c"] == (8192 + 4096, 1, 0)


def test_ncdu_progress_while_reading() -> None:
    progress = quiet()
    parse_ncdu(io.BytesIO(load_fixture("ncdu.json")), job(), Tree(), ErrorLog(), progress)
    assert (progress.dirs, progress.files, progress.bytes) == (7, FILES, APPARENT_TOTAL)


def test_ncdu_terminal_progress() -> None:
    progress = quiet()
    terminal = TerminalProgress("/srv/tree", progress)
    terminal.feed("2136 files /  425.5 MiB\r\n  #1: /srv/tree/a b/c\r\n  #2: /srv/t")
    terminal.feed("ree/x\r\n\x1b[1F\x1b[2K\x1b[1F\x1b[2K3748 files /   1.2 GiB\r\n")
    assert progress.files == 3748
    assert progress.bytes == int(1.2 * 2**30)
    assert progress.current == "a b/c"
    # Then it writes its export, counting from 0 again: the walk is over and keeps its counters.
    terminal.feed("\x1b[1F\x1b[2K120 files /  20.0 MiB\r\n  #1: /srv/tree/z\r\n5000 files /   1.3 GiB\r\n")
    assert (progress.phase, progress.files, progress.current) == ("finishing", 3748, "a b/c")
    assert progress.bytes == int(1.2 * 2**30)


def test_gdu_export_counts_like_du() -> None:
    log = load_fixture("gdu.log").decode().splitlines()
    for measure, total in (("apparent", APPARENT_TOTAL), ("disk", DISK_TOTAL)):
        tree, errors = Tree(), ErrorLog()
        reader = parse_gdu(io.BytesIO(load_fixture("gdu.json")), job(measure), tree, errors,
                           quiet())
        gdu_errors(reader, log, errors)
        assert reader.finished
        folders = paths(tree)
        assert folders[""] == (total, FILES, FOLDERS)
        assert errors.sample == [{"path": "locked", "message": "permission denied"}]
    # The hard link was met first at the root, so "a b" does not count it again.
    assert paths(tree)["a b"] == (4096 + 4096 + 4096 + 8192, 3, 1)


def test_du_output_counts_folders_and_errors() -> None:
    for name, total in (("du-apparent.bin", APPARENT_TOTAL), ("du-disk.bin", DISK_TOTAL)):
        tree, progress = Tree(count_files=False), quiet()
        assert parse_du(io.BytesIO(load_fixture(name)), ROOT, tree, progress)
        folders = paths(tree)
        assert folders[""] == (total, None, FOLDERS)
        assert progress.dirs == 7
        assert "nl\nline" in folders and "uni-ñandú" in folders
    errors = ErrorLog()
    du_error("du: cannot read directory '/srv/tree/locked': Permission denied", ROOT, errors)
    assert errors.sample == [
        {"path": "locked", "message": "cannot read directory: Permission denied"}
    ]


def test_du_reads_records_split_across_chunks() -> None:
    data = load_fixture("du-apparent.bin")

    class Trickle(io.BytesIO):
        def read(self, size: int | None = -1) -> bytes:
            return super().read(7)

    tree = Tree(count_files=False)
    assert parse_du(Trickle(data), ROOT, tree, quiet())
    assert paths(tree)[""][0] == APPARENT_TOTAL


def test_du_names_that_are_not_utf8() -> None:
    data = b"10\t/srv/tree/caf\xe9\x0010\t/srv/tree\x00"
    tree = Tree(count_files=False)
    parse_du(io.BytesIO(data), ROOT, tree, quiet())
    assert "caf\\xe9" in paths(tree)


class Trickle(io.BytesIO):
    """A stream that gives one byte per read, to split every sequence across reads."""

    def read(self, size: int | None = -1) -> bytes:
        return super().read(1)

    def read1(self, size: int | None = -1) -> bytes:
        return super().read(1)


NCDU_LATIN1 = (
    b'[1,2,{"progname":"ncdu","progver":"2.9.1"},\n'
    b'[{"name":"/srv/tree","asize":4096,"dsize":4096,"dev":1},\n'
    b'[{"name":"M\xfasica","asize":4096,"dsize":4096},\n'
    b'{"name":"song\xff.flac","asize":3145728,"dsize":3145728}],\n'
    b'[{"name":"Fotos ni\xf1os","asize":4096,"dsize":4096,"read_error":true}],\n'
    b'[{"name":"caf\xc3\xa9","asize":4096,"dsize":4096}]]]\n'
)


def test_ncdu_names_that_are_not_utf8() -> None:
    """ncdu writes names as they are on disk: bytes that are not UTF-8 become `\\xNN`, as the
    native engine and du write them, even when a read splits them."""
    for stream in (io.BytesIO(NCDU_LATIN1), Trickle(NCDU_LATIN1)):
        tree, errors = Tree(), ErrorLog()
        assert parse_ncdu(stream, job(), tree, errors, quiet())
        folders = paths(tree)
        assert set(folders) == {"", "M\\xfasica", "Fotos ni\\xf1os", "café"}
        assert folders["M\\xfasica"] == (3145728, 1, 0)
        assert errors.sample == [{"path": "Fotos ni\\xf1os", "message": "read error"}]


def test_gdu_errors_of_names_that_are_not_utf8() -> None:
    """gdu's export writes U+FFFD for each byte that is not UTF-8 and its log a Go escape: the
    reason in the log still reaches the folder of the export."""
    export = (
        b'[1,2,{"progname":"gdu"},\n[{"name":"/srv/tree","asize":4096,"dsize":4096},\n'
        b'[{"name":"Fotos ni\xef\xbf\xbdos","asize":4096,"dsize":4096,"read_error":true}],\n'
        b'[{"name":"a\xef\xbf\xbd\xef\xbf\xbdb","asize":4096,"dsize":4096,"read_error":true}],\n'
        b'[{"name":"q\'uo \\"x\\"","asize":4096,"dsize":4096,"read_error":true}]]]\n'
    )
    log = [
        'time="2026-11-15T22:06:14Z" level=info msg="open /srv/tree/Fotos ni\\xf1os: permission denied"',
        'time="2026-11-15T22:06:14Z" level=info msg="open /srv/tree/a\\xe2\\x82b: permission denied"',
        'time="2026-11-15T22:06:14Z" level=info msg="open /srv/tree/q\'uo \\"x\\": permission denied"',
    ]
    tree, errors = Tree(), ErrorLog()
    reader = parse_gdu(io.BytesIO(export), job(), tree, errors, quiet())
    gdu_errors(reader, log, errors)
    assert errors.sample == [
        {"path": "Fotos ni\ufffdos", "message": "permission denied"},
        {"path": "a\ufffd\ufffdb", "message": "permission denied"},
        {"path": 'q\'uo "x"', "message": "permission denied"},
    ]


def test_du_errors_name_folders_like_the_other_engines() -> None:
    """du starts its messages with its name as it was run and quotes file names like a shell."""
    errors = ErrorLog()
    for line in (
        "/usr/bin/du: cannot read directory '/srv/tree/Fotos ni'$'\\361''os': Permission denied",
        "/usr/bin/gnudu: cannot read directory \"/srv/tree/q'uo\": Permission denied",
        "du: cannot read directory '/srv/tree/caf'$'\\303\\251''/locked': Permission denied",
        "du: cannot access '/srv/tree/new'$'\\n''line': No such file or directory",
        "/usr/bin/du: fts_read failed: '/srv/tree': Input/output error",
    ):
        du_error(line, ROOT, errors)
    assert [(error["path"], error["message"]) for error in errors.sample] == [
        ("Fotos ni\\xf1os", "cannot read directory: Permission denied"),
        ("q'uo", "cannot read directory: Permission denied"),
        ("café/locked", "cannot read directory: Permission denied"),
        ("new\nline", "cannot access: No such file or directory"),
        ("", "fts_read failed: Input/output error"),
    ]
    assert shell_unquote("'it'\\''s'") == b"it's"
    assert shell_unquote("\"a \\\"b\\\" \\\\ c\"") == b'a "b" \\ c'
    assert shell_unquote("$'\\x41\\t\\101'") == b"A\tA"


def test_commands() -> None:
    options = job("disk", threads=4)
    assert du_command(options, "du") == [
        "du", "-0", "-B1", "-x", "--exclude=.snapshot", "--", ROOT
    ]
    assert ncdu_command(options, "ncdu") == [
        "ncdu", "--ignore-config", "-1", "-o-", "-x", "--exclude", ".snapshot", "-t", "4", ROOT
    ]
    assert ncdu_command(job(threads=1), "ncdu")[2] == "-0"
    assert gdu_command(options, "gdu", "ignore", "log") == [
        "gdu", "-n", "-p", "-c", "-o-", "-l", "log", "-x", "-X", "ignore", "-m", "4", ROOT
    ]
    assert "--sequential" in gdu_command(job(threads=1), "gdu", None, "log")
    assert gdu_command(job(threads=16), "gdu", None, "log")[-2] == "8"


def test_gdu_patterns_are_full_path_regexes() -> None:
    import re

    patterns = ignore_patterns(job(excludes=["#snap*", "a b", "x?y"]))
    assert patterns == [r".*/\#snap[^/]*", r".*/a\x20b", r".*/x[^/]y"]
    assert re.fullmatch(patterns[0], "/srv/tree/#snapshot")
    assert not re.fullmatch(patterns[0], "/srv/tree/#snapshot/inside")
    assert re.fullmatch(patterns[1], "/srv/tree/deep/a b")
