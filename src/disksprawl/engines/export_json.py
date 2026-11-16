"""Reads the JSON export format of ncdu, which gdu also writes, as a stream (ijson).

The export is `[1, 2, {metadata}, DIR]`, where `DIR` is an array whose first element is the folder's
own information and whose other elements are its files (objects) and its subfolders (arrays). Items
are flat objects; folders skipped by an exclusion appear as objects with an `excluded` key.

ncdu writes file names as they are, so its export is not valid UTF-8 when a name is not: every such
byte is turned into the escape `\\xNN` before parsing, the text `display_name` gives the same name.
gdu writes U+FFFD for each of those bytes itself.
"""

import codecs
from collections.abc import Callable
from typing import IO, Any

import ijson

Item = dict[str, Any]
ESCAPE_BYTES = "disksprawl-json-bytes"


def _escape_bytes(error: UnicodeError) -> tuple[str, int]:
    if not isinstance(error, UnicodeDecodeError):
        raise error
    invalid = error.object[error.start : error.end]
    # `\\xNN` in JSON text is the string `\xNN`.
    return "".join(f"\\\\x{byte:02x}" for byte in invalid), error.end


codecs.register_error(ESCAPE_BYTES, _escape_bytes)


class ValidUtf8:
    """A binary stream of JSON whose bytes that are not UTF-8 become `\\xNN` escapes in strings."""

    def __init__(self, stream: IO[bytes]):
        self.decoder = codecs.getincrementaldecoder("utf-8")(errors=ESCAPE_BYTES)
        # read1 returns what has arrived, so a slow scan still shows its progress.
        self.read_some = getattr(stream, "read1", stream.read)

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        while True:
            chunk = self.read_some(size if size > 0 else 65536)
            text = self.decoder.decode(chunk, final=not chunk)
            if text or not chunk:
                return text.encode("utf-8")


def walk_export(
    stream: IO[bytes],
    on_folder: Callable[[Item], None],
    on_file: Callable[[Item], None],
    on_folder_end: Callable[[], None],
) -> None:
    depth = 0
    expect_folder = False
    current: Item | None = None
    key: str | None = None
    for event, value in ijson.basic_parse(ValidUtf8(stream), use_float=True):
        if event == "start_array":
            depth += 1
            expect_folder = depth >= 2
        elif event == "end_array":
            if depth >= 2:
                on_folder_end()
            depth -= 1
        elif event == "start_map":
            current = {}
            key = None
        elif event == "map_key":
            key = value
        elif event == "end_map":
            item, current = current, None
            if depth < 2 or item is None:
                continue
            if expect_folder:
                expect_folder = False
                on_folder(item)
            else:
                on_file(item)
        elif current is not None and key is not None:
            current[key] = value
            key = None
