"""Reads the JSON export format of ncdu, which gdu also writes, as a stream (ijson).

The export is `[1, 2, {metadata}, DIR]`, where `DIR` is an array whose first element is the folder's
own information and whose other elements are its files (objects) and its subfolders (arrays). Items
are flat objects; folders skipped by an exclusion appear as objects with an `excluded` key.
"""

from collections.abc import Callable
from typing import IO, Any

import ijson

Item = dict[str, Any]


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
    for event, value in ijson.basic_parse(stream, use_float=True):
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
