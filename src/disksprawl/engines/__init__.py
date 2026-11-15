"""The scan engines and their detection: each external binary is looked up on PATH at startup."""

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass

from .common import ENGINES

MAX_THREADS = {"native": 16, "du": 1, "ncdu": 16, "gdu": 8}
# How a running scan reports progress: live counters, finished folders (du), live only with one
# thread (ncdu), or nothing until the end (gdu).
PROGRESS = {"native": "live", "du": "folders", "ncdu": "single_thread", "gdu": "none"}
# GNU du first; `gnudu` is GNU du on systems whose `du` is another implementation.
DU_CANDIDATES = ("du", "gnudu")


@dataclass(frozen=True)
class EngineInfo:
    id: str
    available: bool
    version: str | None
    binary: str | None
    max_threads: int
    progress: str


def _version_output(binary: str) -> str:
    try:
        completed = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            timeout=10,
            check=False,
            env={"LC_ALL": "C", "PATH": "/usr/local/bin:/usr/bin:/bin"},
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (completed.stdout or completed.stderr).decode("utf-8", "replace")


def _detect_du() -> EngineInfo:
    for candidate in DU_CANDIDATES:
        binary = shutil.which(candidate)
        if not binary:
            continue
        first = _version_output(binary).splitlines()[:1]
        if first and "GNU coreutils" in first[0]:
            return EngineInfo("du", True, first[0].split()[-1], binary, 1, PROGRESS["du"])
    return EngineInfo("du", False, None, None, 1, PROGRESS["du"])


def _detect_ncdu() -> EngineInfo:
    binary = shutil.which("ncdu")
    match = re.search(r"ncdu (\d+)\.(\d+)(\S*)", _version_output(binary)) if binary else None
    if not binary or not match:
        return EngineInfo("ncdu", False, None, None, MAX_THREADS["ncdu"], PROGRESS["ncdu"])
    version = f"{match[1]}.{match[2]}{match[3]}"
    # Threads (-t) arrived in ncdu 2.5.
    threaded = (int(match[1]), int(match[2])) >= (2, 5)
    return EngineInfo(
        "ncdu", True, version, binary, MAX_THREADS["ncdu"] if threaded else 1, PROGRESS["ncdu"]
    )


def _detect_gdu() -> EngineInfo:
    binary = shutil.which("gdu")
    match = re.search(r"Version:\s*v?(\S+)", _version_output(binary)) if binary else None
    if not binary or not match:
        return EngineInfo("gdu", False, None, None, MAX_THREADS["gdu"], PROGRESS["gdu"])
    return EngineInfo("gdu", True, match[1], binary, MAX_THREADS["gdu"], PROGRESS["gdu"])


def detect_engines() -> dict[str, EngineInfo]:
    native = EngineInfo(
        "native",
        True,
        f"Python {sys.version_info.major}.{sys.version_info.minor}",
        None,
        MAX_THREADS["native"],
        PROGRESS["native"],
    )
    found = {"native": native, "du": _detect_du(), "ncdu": _detect_ncdu(), "gdu": _detect_gdu()}
    return {engine: found[engine] for engine in ENGINES}
