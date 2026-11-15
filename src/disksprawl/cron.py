"""Schedules in cron format (5 fields), evaluated in the server's time zone (TZ).

Day-of-week numbers follow standard cron (0 and 7 are Sunday, 1 is Monday). APScheduler counts from
Monday instead, so the field is translated to day names before it reaches APScheduler. When both
the day of the month and the day of the week are restricted, both must match.
"""

from datetime import datetime, timedelta, tzinfo

from apscheduler.triggers.cron import CronTrigger

from .db import utcnow
from .errors import AppError

MAX_LENGTH = 120
DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _weekday(text: str) -> int:
    if text in DAY_NAMES:
        return DAY_NAMES.index(text)
    if text.isdigit() and int(text) <= 7:
        return int(text)
    raise ValueError(f"Invalid day of week {text!r}")


def weekdays(field: str) -> str:
    """A standard cron day-of-week field as APScheduler day names (`1-5` → `mon,…,fri`)."""
    if field == "*":
        return field
    days: set[int] = set()
    for item in field.lower().split(","):
        base, slash, step_text = item.partition("/")
        if slash and not (step_text.isdigit() and 1 <= int(step_text) <= 7):
            raise ValueError(f"Invalid step {step_text!r}")
        step = int(step_text) if slash else 1
        if base == "*":
            start, end = 0, 6
        elif "-" in base:
            first, last = base.split("-", 1)
            start, end = _weekday(first), _weekday(last)
        else:
            start = _weekday(base)
            end = 6 if slash else start
        if start > end:
            raise ValueError(f"Invalid range {base!r}")
        days.update(day % 7 for day in range(start, end + 1, step))
    return ",".join(DAY_NAMES[day] for day in sorted(days))


def trigger(expression: str, zone: tzinfo) -> CronTrigger:
    text = " ".join(expression.split())
    fields = text.split(" ")
    if not text or len(text) > MAX_LENGTH or len(fields) != 5:
        raise AppError("invalid_cron", fields={"cron": "invalid_cron"})
    minute, hour, day, month, day_of_week = fields
    try:
        return CronTrigger(
            minute=minute,
            hour=hour,
            day=day,
            month=month,
            day_of_week=weekdays(day_of_week),
            timezone=zone,
        )
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
