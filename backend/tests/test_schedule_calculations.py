from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.application.scheduling.calculations import initial_fire_time, next_fire_time
from app.common.schemas.schedules import ScheduleSpec

UTC = timezone.utc


def test_once_schedule_preserves_absolute_time():
    schedule = ScheduleSpec(
        type="once",
        at=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
    )

    assert initial_fire_time(
        schedule,
        "Asia/Shanghai",
        now=datetime(2026, 8, 1, 8, 0, tzinfo=UTC),
    ) == datetime(2026, 8, 1, 9, 0, tzinfo=UTC)
    assert (
        next_fire_time(
            schedule,
            "UTC",
            after=datetime(2026, 8, 1, 9, 0, tzinfo=UTC),
        )
        is None
    )


def test_interval_advances_from_anchor_without_execution_drift():
    schedule = ScheduleSpec(
        type="interval",
        interval_seconds=300,
        anchor_at=datetime(2026, 8, 1, 8, 0, tzinfo=UTC),
    )

    assert next_fire_time(
        schedule,
        "UTC",
        after=datetime(2026, 8, 1, 8, 12, 30, tzinfo=UTC),
    ) == datetime(2026, 8, 1, 8, 15, tzinfo=UTC)


def test_cron_uses_iana_timezone():
    schedule = ScheduleSpec(type="cron", expression="0 9 * * *")

    assert next_fire_time(
        schedule,
        "Asia/Shanghai",
        after=datetime(2026, 8, 1, 0, 30, tzinfo=UTC),
    ) == datetime(2026, 8, 1, 1, 0, tzinfo=UTC)


def test_cron_handles_dst_spring_gap_and_fall_overlap_deterministically():
    spring = ScheduleSpec(type="cron", expression="30 2 * * *")
    fall = ScheduleSpec(type="cron", expression="30 1 * * *")

    # 02:30 does not exist on the spring-forward day; croniter advances to
    # the first valid local instant after the gap.
    assert next_fire_time(
        spring,
        "America/New_York",
        after=datetime(2026, 3, 8, 6, 0, tzinfo=UTC),
    ) == datetime(2026, 3, 8, 7, 0, tzinfo=UTC)
    # The repeated 01:30 resolves to the first occurrence. The persisted UTC
    # fire time remains the idempotency boundary.
    assert next_fire_time(
        fall,
        "America/New_York",
        after=datetime(2026, 11, 1, 4, 0, tzinfo=UTC),
    ) == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "cron", "expression": "* * * * * *"},
        {"type": "once", "at": "2026-08-01T09:00:00"},
        {"type": "interval", "interval_seconds": 59},
    ],
)
def test_invalid_schedule_shapes_are_rejected(payload):
    with pytest.raises(ValidationError):
        ScheduleSpec.model_validate(payload)
