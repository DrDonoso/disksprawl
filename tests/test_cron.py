"""Cron schedules: validation, next runs in the server's time zone and missed runs."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from disksprawl.cron import missed_run, next_runs, normalize
from disksprawl.errors import AppError

MADRID = ZoneInfo("Europe/Madrid")


def test_normalize() -> None:
    assert normalize(None, MADRID) is None
    assert normalize("   ", MADRID) is None
    assert normalize(" 0  3 * * * ", MADRID) == "0 3 * * *"
    for invalid in ("0 3 * *", "0 25 * * *", "@daily", "0 3 * * * *", "x" * 200):
        with pytest.raises(AppError):
            normalize(invalid, MADRID)


def test_next_runs_follow_the_time_zone_across_daylight_saving() -> None:
    # 03:00 in Madrid is 02:00 UTC in winter and 01:00 UTC in summer (from 2026-03-29).
    runs = next_runs("0 3 * * *", MADRID, after=datetime(2026, 3, 27, 12, tzinfo=UTC))
    assert [run.astimezone(UTC).hour for run in runs] == [2, 1, 1]
    assert all(run.astimezone(MADRID).hour == 3 for run in runs)


def test_missed_run() -> None:
    now = datetime(2026, 11, 15, 3, 30, tzinfo=UTC)
    zone = ZoneInfo("Etc/UTC")
    assert missed_run("0 3 * * *", zone, datetime(2026, 11, 15, 2, 30, tzinfo=UTC), now) == (
        datetime(2026, 11, 15, 3, 0, tzinfo=zone)
    )
    assert missed_run("0 3 * * *", zone, datetime(2026, 11, 15, 3, 0, 5, tzinfo=UTC), now) is None
    assert missed_run("0 4 * * *", zone, datetime(2026, 11, 15, 2, 30, tzinfo=UTC), now) is None
