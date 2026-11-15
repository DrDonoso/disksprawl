"""Schedules in cron format (5 fields), evaluated in the server's time zone (TZ)."""

from datetime import datetime, timedelta, tzinfo

from apscheduler.triggers.cron import CronTrigger

from .db import utcnow
from .errors import AppError

MAX_LENGTH = 120


def trigger(expression: str, zone: tzinfo) -> CronTrigger:
    text = " ".join(expression.split())
    if not text or len(text) > MAX_LENGTH or len(text.split(" ")) != 5:
        raise AppError("invalid_cron", fields={"cron": "invalid_cron"})
    try:
        return CronTrigger.from_crontab(text, timezone=zone)
    except (ValueError, KeyError):
        raise AppError("invalid_cron", fields={"cron": "invalid_cron"}) from None


def normalize(expression: str | None, zone: tzinfo) -> str | None:
    """A valid expression with single spaces, or None for manual scans only."""
    if expression is None or not expression.strip():
        return None
    trigger(expression, zone)
    return " ".join(expression.split())


def next_runs(
    expression: str, zone: tzinfo, count: int = 3, after: datetime | None = None
) -> list[datetime]:
    schedule = trigger(expression, zone)
    runs: list[datetime] = []
    previous = None
    now = after or utcnow()
    while len(runs) < count:
        moment = schedule.get_next_fire_time(previous, now)
        if moment is None:
            break
        runs.append(moment)
        previous = moment
        now = moment + timedelta(seconds=1)
    return runs


def missed_run(
    expression: str, zone: tzinfo, since: datetime, now: datetime | None = None
) -> datetime | None:
    """The last scheduled moment in (since, now], if any: a run missed while the app was down."""
    schedule = trigger(expression, zone)
    current = now or utcnow()
    moment = schedule.get_next_fire_time(None, since + timedelta(seconds=1))
    last = None
    while moment is not None and moment <= current:
        last = moment
        moment = schedule.get_next_fire_time(moment, moment + timedelta(seconds=1))
    return last
