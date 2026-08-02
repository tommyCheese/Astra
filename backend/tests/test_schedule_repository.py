from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import ScheduledJobRecord, TaskRecord
from app.repositories.schedules import (
    ScheduleRepository,
    ScheduleVersionConflictError,
    SystemManagedScheduleError,
)
from app.schemas.schedules import ScheduledJobCreate, ScheduledJobUpdate

UTC = timezone.utc


def job_payload(**overrides):
    payload = {
        "name": "Morning brief",
        "target_task_id": "task-1",
        "prompt": "Summarize overnight updates",
        "schedule": {"type": "cron", "expression": "0 9 * * *"},
        "timezone": "Asia/Shanghai",
        "execution": {"permission_bundle": {"token": "signed"}},
    }
    payload.update(overrides)
    return ScheduledJobCreate.model_validate(payload)


@pytest.mark.asyncio
async def test_create_update_pause_and_resume_schedule(session):
    repo = ScheduleRepository(session)
    now = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    job = await repo.create(job_payload(), now=now)

    assert job.version == 1
    assert job.target_task_id == "task-1"
    assert job.next_fire_at == datetime(2026, 8, 1, 1, 0, tzinfo=UTC)

    job = await repo.update(
        job.id,
        ScheduledJobUpdate(version=1, name="Daily brief"),
        now=now,
    )
    assert job.name == "Daily brief"
    assert job.version == 2

    job = await repo.set_enabled(job.id, enabled=False, version=2, now=now)
    assert job.enabled is False
    assert job.next_fire_at is None

    job = await repo.set_enabled(job.id, enabled=True, version=3, now=now)
    assert job.enabled is True
    assert job.next_fire_at == datetime(2026, 8, 1, 1, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_stale_schedule_update_is_rejected(session):
    repo = ScheduleRepository(session)
    job = await repo.create(
        job_payload(),
        now=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
    )
    await repo.update(job.id, ScheduledJobUpdate(version=1, name="New name"))

    with pytest.raises(ScheduleVersionConflictError):
        await repo.update(job.id, ScheduledJobUpdate(version=1, name="Stale"))


@pytest.mark.asyncio
async def test_system_managed_schedule_is_protected(session):
    job = ScheduledJobRecord(
        name="Heartbeat",
        kind="heartbeat",
        system_key="heartbeat:task-1",
        system_managed=True,
        prompt="HEARTBEAT",
        schedule_type="interval",
        schedule={"type": "interval", "interval_seconds": 1800},
        timezone="UTC",
        execution={},
        heartbeat={},
    )
    session.add(job)
    await session.commit()

    with pytest.raises(SystemManagedScheduleError):
        await ScheduleRepository(session).update(
            job.id,
            ScheduledJobUpdate(version=1, name="Mutated"),
        )


@pytest.mark.asyncio
async def test_claim_due_creates_one_run_and_advances_next_fire(session):
    task = TaskRecord(title="Target", description="Scheduled task target")
    session.add(task)
    await session.commit()

    repo = ScheduleRepository(session)
    first_fire = datetime(2026, 8, 1, 1, 0, tzinfo=UTC)
    job = await repo.create(
        job_payload(target_task_id=task.id),
        now=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
    )

    claimed = await repo.claim_due(
        claimed_by="test-scheduler",
        lease_seconds=30,
        batch_size=10,
        now=first_fire,
    )

    assert len(claimed) == 1
    assert claimed[0].job_id == job.id
    assert claimed[0].scheduled_for == first_fire
    refreshed = await repo.require(job.id)
    assert refreshed.last_fire_at == first_fire
    assert refreshed.next_fire_at == datetime(2026, 8, 2, 1, 0, tzinfo=UTC)
    assert refreshed.lease_owner is None

    duplicate = await repo.claim_due(
        claimed_by="second-scheduler",
        lease_seconds=30,
        batch_size=10,
        now=first_fire,
    )
    assert duplicate == []


@pytest.mark.asyncio
async def test_recover_claimed_run_after_scheduler_restart(session):
    task = TaskRecord(title="Target", description="Scheduled task target")
    session.add(task)
    await session.commit()

    repo = ScheduleRepository(session)
    claimed_at = datetime(2026, 8, 2, 0, 0, tzinfo=UTC)
    job = await repo.create(
        job_payload(target_task_id=task.id),
        now=datetime(2026, 8, 1, 0, 0, tzinfo=UTC),
    )
    schedule_run = await repo.manual_trigger(job, now=claimed_at)

    assert await repo.recover_claimed(
        claimed_by="new-scheduler",
        stale_after_seconds=30,
        batch_size=10,
        now=claimed_at + timedelta(seconds=29),
    ) == []

    recovered = await repo.recover_claimed(
        claimed_by="new-scheduler",
        stale_after_seconds=30,
        batch_size=10,
        now=claimed_at + timedelta(seconds=30),
    )
    assert [item.id for item in recovered] == [schedule_run.id]
    assert recovered[0].claimed_by == "new-scheduler"
