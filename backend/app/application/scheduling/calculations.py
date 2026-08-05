from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from croniter import croniter

from app.common.schemas.schedules import ScheduleSpec, ScheduleType

UTC = timezone.utc


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("调度时间必须包含时区")
    return value.astimezone(UTC)


def required_datetime(value: datetime | None, field_name: str) -> datetime:
    if value is None:
        raise ValueError(f"{field_name} is required for this schedule type")
    return value


def next_fire_time(
    schedule: ScheduleSpec,
    timezone_name: str,
    *,
    after: datetime,
) -> datetime | None:
    """Return the first logical fire time strictly after ``after``."""
    after_utc = as_utc(after)
    zone = ZoneInfo(timezone_name)

    if schedule.type == ScheduleType.once:
        fire_at = as_utc(required_datetime(schedule.at, "at"))
        return fire_at if fire_at > after_utc else None

    if schedule.type == ScheduleType.interval:
        interval = timedelta(seconds=schedule.interval_seconds or 0)
        anchor = as_utc(schedule.anchor_at or after_utc)
        if anchor > after_utc:
            return anchor
        elapsed = after_utc - anchor
        periods = elapsed // interval + 1
        return anchor + periods * interval

    local_after = after_utc.astimezone(zone)
    next_local = croniter(schedule.expression, local_after).get_next(datetime)
    if next_local.tzinfo is None:
        next_local = next_local.replace(tzinfo=zone)
    return next_local.astimezone(UTC)


def initial_fire_time(
    schedule: ScheduleSpec,
    timezone_name: str,
    *,
    now: datetime,
) -> datetime | None:
    """Calculate the initial fire without drifting interval anchors."""
    now_utc = as_utc(now)
    if schedule.type == ScheduleType.once:
        fire_at = as_utc(required_datetime(schedule.at, "at"))
        return fire_at if fire_at >= now_utc else None
    if schedule.type == ScheduleType.interval and schedule.anchor_at is None:
        return now_utc + timedelta(seconds=schedule.interval_seconds or 0)
    return next_fire_time(schedule, timezone_name, after=now_utc)
