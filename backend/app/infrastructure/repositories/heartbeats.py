from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.scheduling.calculations import initial_fire_time
from app.common.schemas.schedules import HeartbeatConfig, ScheduledJobKind, ScheduleSpec
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.scheduling import ScheduledJobRecord
from app.infrastructure.repositories.schedules import ScheduleNotFoundError


class HeartbeatRepository:
    """Persistence owner for the system-managed global heartbeat."""

    GLOBAL_KEY = "heartbeat:global"

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self) -> ScheduledJobRecord | None:
        return await self.session.scalar(
            select(ScheduledJobRecord).where(
                ScheduledJobRecord.system_key == self.GLOBAL_KEY,
                ScheduledJobRecord.deleted_at.is_(None),
            )
        )

    async def upsert(
        self,
        payload: HeartbeatConfig,
        *,
        owner_principal: str | None = None,
        now: datetime | None = None,
    ) -> ScheduledJobRecord:
        reference = (now or utc_now()).astimezone(timezone.utc)
        job = await self.get()
        schedule = {"type": "interval", "interval_seconds": payload.interval_seconds}
        next_fire_at = self._next_fire_at(payload, schedule, reference)
        heartbeat = {
            "active_hours": (
                payload.active_hours.model_dump(mode="json") if payload.active_hours else None
            ),
            "prompt": payload.prompt,
        }
        if job is None:
            job = self._new_job(
                payload,
                schedule,
                heartbeat,
                next_fire_at,
                owner_principal,
                reference,
            )
            self.session.add(job)
        else:
            self._update_job(job, payload, schedule, heartbeat, next_fire_at, reference)
        await self.session.flush()
        await self.session.commit()
        return job

    @staticmethod
    def _next_fire_at(payload, schedule, reference):
        if not payload.enabled:
            return None
        return initial_fire_time(
            ScheduleSpec.model_validate(schedule), payload.timezone, now=reference
        )

    @classmethod
    def _new_job(
        cls, payload, schedule, heartbeat, next_fire_at, owner_principal, reference
    ) -> ScheduledJobRecord:
        return ScheduledJobRecord(
            name="Heartbeat",
            kind=ScheduledJobKind.heartbeat.value,
            system_key=cls.GLOBAL_KEY,
            system_managed=True,
            owner_principal=owner_principal,
            target_task_id=payload.target_task_id,
            prompt=payload.prompt,
            schedule_type="interval",
            schedule=schedule,
            timezone=payload.timezone,
            enabled=payload.enabled,
            misfire_policy="skip",
            misfire_grace_seconds=min(300, payload.interval_seconds),
            overlap_policy="skip",
            execution=payload.execution.model_dump(mode="json"),
            heartbeat=heartbeat,
            next_fire_at=next_fire_at,
            version=1,
            created_at=reference,
            updated_at=reference,
        )

    @classmethod
    def _update_job(cls, job, payload, schedule, heartbeat, next_fire_at, reference) -> None:
        job.system_key = cls.GLOBAL_KEY
        job.target_task_id = payload.target_task_id
        job.prompt = payload.prompt
        job.schedule_type = "interval"
        job.schedule = schedule
        job.timezone = payload.timezone
        job.enabled = payload.enabled
        job.misfire_grace_seconds = min(300, payload.interval_seconds)
        job.execution = payload.execution.model_dump(mode="json")
        job.heartbeat = heartbeat
        job.next_fire_at = next_fire_at
        job.lease_owner = None
        job.lease_expires_at = None
        job.version += 1
        job.updated_at = reference

    async def disable(self, *, now: datetime | None = None) -> ScheduledJobRecord:
        job = await self.get()
        if job is None:
            raise ScheduleNotFoundError(self.GLOBAL_KEY)
        reference = (now or utc_now()).astimezone(timezone.utc)
        job.enabled = False
        job.next_fire_at = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.version += 1
        job.updated_at = reference
        await self.session.commit()
        return job
