"""Directories live inside SCAN_ROOT: paths are checked after resolving symbolic links, so a link
inside /data can never lead the scanner or the folder picker anywhere else."""

import os
import posixpath
from pathlib import Path

from .errors import AppError

MAX_BROWSE_ENTRIES = 2000


def normalize(path: str) -> str:
    """An absolute path without `.`, `..`, repeated or trailing slashes (lexically)."""
    if not path or "\0" in path or not path.startswith("/"):
        raise AppError("path_invalid", fields={"path": "path_invalid"})
    return posixpath.normpath(path) if path != "/" else "/"


def inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def resolve(path: str, scan_root: Path) -> Path:
    """The real folder behind `path`, which must exist inside the scan root."""
    root = scan_root.resolve()
    try:
        real = Path(normalize(path)).resolve(strict=True)
    except (FileNotFoundError, NotADirectoryError):
        raise AppError("path_not_found", fields={"path": "path_not_found"}) from None
    except (OSError, RuntimeError):
        raise AppError("path_unreadable", fields={"path": "path_unreadable"}) from None
    if not inside(real, root):
        raise AppError("path_outside_root", fields={"path": "path_outside_root"})
    if not real.is_dir():
        raise AppError("path_not_directory", fields={"path": "path_not_directory"})
    return real


def browse(path: str | None, scan_root: Path) -> tuple[str, str | None, list[tuple[str, str]]]:
    """(folder, parent, [(name, path)…]) of the subfolders of a folder inside the scan root.

    Paths are shown as typed below the root (`/data/media`), never as their resolved targets.
    """
    root_text = normalize(str(scan_root))
    folder = normalize(path) if path else root_text
    if not (folder == root_text or folder.startswith(root_text.rstrip("/") + "/")):
        raise AppError("path_outside_root", fields={"path": "path_outside_root"})
    real = resolve(folder, scan_root)
    entries: list[tuple[str, str]] = []
    try:
        with os.scandir(real) as listing:
            for entry in listing:
                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                    target = Path(entry.path).resolve(strict=True)
                except OSError:
                    continue
                if inside(target, scan_root.resolve()):
                    entries.append((entry.name, posixpath.join(folder, entry.name)))
    except PermissionError:
        raise AppError("path_unreadable", fields={"path": "path_unreadable"}) from None
    entries.sort(key=lambda item: (item[0].casefold(), item[0]))
    parent = None if folder == root_text else posixpath.dirname(folder)
    return folder, parent, entries[:MAX_BROWSE_ENTRIES]
