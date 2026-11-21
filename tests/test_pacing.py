"""Pacing of external engines: SIGSTOP/SIGCONT cycles that rest the pace's share of the time."""

import subprocess
import sys
import time
from pathlib import Path

import pytest

from disksprawl.engines.pacing import CYCLE, Pacer, cycle


def test_cycles() -> None:
    assert cycle("max") is None
    assert cycle("balanced") == (pytest.approx(CYCLE * 0.75), pytest.approx(CYCLE * 0.25))
    assert cycle("gentle") == (pytest.approx(CYCLE / 2), pytest.approx(CYCLE / 2))


def stopped(pid: int) -> bool:
    fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
    return fields[0] in ("T", "t")


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="needs /proc")
@pytest.mark.parametrize(("pace", "share"), [("balanced", 0.25), ("gentle", 0.5)])
def test_a_paced_process_rests_its_share_of_the_time(pace: str, share: float) -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pacer = Pacer(child.pid, pace)
    try:
        started = time.monotonic()
        pacer.start()
        samples = []
        deadline = time.monotonic() + 3 * CYCLE
        while time.monotonic() < deadline:
            samples.append(stopped(child.pid))
            time.sleep(0.02)
        pacer.stop()
        elapsed = time.monotonic() - started
        assert not stopped(child.pid), "the process must run again after pacing stops"
    finally:
        child.kill()
        child.wait()
    assert sum(samples) / len(samples) == pytest.approx(share, abs=0.12)
    # The time it was frozen, for the scan's log: within one rest of the pace's share.
    assert pacer.rested == pytest.approx(share * elapsed, abs=CYCLE * share + 0.2)


def test_stopping_pacing_of_a_finished_process_is_harmless() -> None:
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    pacer = Pacer(child.pid, "gentle")
    pacer.start()
    pacer.stop()
