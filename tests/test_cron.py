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


def test_days_of_week_follow_standard_cron() -> None:
    sunday = datetime(2026, 11, 15, 12, tzinfo=UTC)  # a Sunday
    zone = ZoneInfo("Etc/UTC")

    def days(expression: str) -> list[str]:
        return [run.strftime("%a") for run in next_runs(expression, zone, count=5, after=sunday)]

    assert days("0 3 * * 1") == days("0 3 * * mon") == ["Mon"] * 5
    assert days("0 3 * * 0")[0] == days("0 3 * * 7")[0] == days("0 3 * * sun")[0] == "Sun"
    assert days("0 3 * * 1-5") == ["Mon", "Tue", "Wed", "Thu", "Fri"]
    assert days("0 3 * * 6,0") == ["Sat", "Sun", "Sat", "Sun", "Sat"]
    assert days("0 3 * * */2") == ["Tue", "Thu", "Sat", "Sun", "Tue"]
    for invalid in ("0 3 * * 8", "0 3 * * 5-1", "0 3 * * x", "0 3 * * 1/0"):
        with pytest.raises(AppError):
            normalize(invalid, zone)


def test_missed_run() -> None:
    now = datetime(2026, 11, 15, 3, 30, tzinfo=UTC)
    zone = ZoneInfo("Etc/UTC")
    assert missed_run("0 3 * * *", zone, datetime(2026, 11, 15, 2, 30, tzinfo=UTC), now) == (
        datetime(2026, 11, 15, 3, 0, tzinfo=zone)
    )
    assert missed_run("0 3 * * *", zone, datetime(2026, 11, 15, 3, 0, 5, tzinfo=UTC), now) is None
    assert missed_run("0 4 * * *", zone, datetime(2026, 11, 15, 2, 30, tzinfo=UTC), now) is None
