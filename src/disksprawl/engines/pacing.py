"""Pacing of du, ncdu and gdu: the process is frozen (SIGSTOP) and resumed (SIGCONT) in short
cycles, so it rests the pace's share of the time without knowing about it."""

import os
import signal
import threading
from collections.abc import Callable

from .common import PAUSE_SHARE

# Seconds of each run/rest cycle: short, so the load on the storage stays even.
CYCLE = 2.0


def cycle(pace: str) -> tuple[float, float] | None:
    """(running, resting) seconds of a cycle; None when the pace never rests."""
    share = PAUSE_SHARE[pace]
    if not share:
        return None
    return CYCLE * (1 - share), CYCLE * share


class Pacer:
    def __init__(
        self,
        pid: int,
        pace: str,
        *,
        kill: Callable[[int, int], None] = os.kill,
    ):
        self.pid = pid
        self.timing = cycle(pace)
        self.kill = kill
        self.stopped = threading.Event()
        self.paused = False
        self.lock = threading.Lock()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.timing is None:
            return
        self.thread = threading.Thread(target=self._run, name="pacer", daemon=True)
        self.thread.start()

    def _signal(self, number: int) -> bool:
        try:
            self.kill(self.pid, number)
        except ProcessLookupError:
            return False
        return True

    def _run(self) -> None:
        assert self.timing is not None
        running, resting = self.timing
        while not self.stopped.wait(running):
            with self.lock:
                if self.stopped.is_set() or not self._signal(signal.SIGSTOP):
                    return
                self.paused = True
            self.stopped.wait(resting)
            with self.lock:
                self.paused = False
                if not self._signal(signal.SIGCONT):
                    return

    def stop(self) -> None:
        """Ends pacing and makes sure the process is running."""
        self.stopped.set()
        with self.lock:
            if self.paused:
                self._signal(signal.SIGCONT)
                self.paused = False
        if self.thread:
            self.thread.join()
