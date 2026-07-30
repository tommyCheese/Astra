from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ScheduledJobRecord, ScheduledJobRunRecord, utc_now
from app.scheduling.calculations import initial_fire_time
from app.schemas.schedules import (
    HeartbeatConfig,
    ScheduledJobCreate,
    ScheduledJobKind,
    ScheduledJobUpdate,
)


class ScheduleNotFoundError(LookupError):
    pass


class ScheduleVersionConflictError(RuntimeError):
    pass


class SystemManagedScheduleError(RuntimeError):
    pass


class ScheduleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        payload: ScheduledJobCreate,
        *,
        owner_principal: str | None = None,
        now: datetime | None = None,
        commit: bool = True,
    ) -> ScheduledJobRecord:
        reference = (now or utc_now()).astimezone(timezone.utc)
        job = ScheduledJobRecord(
            name=payload.name,
            kind=ScheduledJobKind.agent.value,
            system_managed=False,
            owner_principal=owner_principal,
            target_task_id=payload.target_task_id,
            prompt=payload.prompt,
            schedule_type=payload.schedule.type.value,
            schedule=payload.schedule.model_dump(mode="json", exclude_none=True),
            timezone=payload.timezone,
            enabled=payload.enabled,
            misfire_policy=payload.misfire_policy.value,
            misfire_grace_seconds=payload.misfire_grace_seconds,
            overlap_policy=payload.overlap_policy.value,
            execution=payload.execution.model_dump(mode="json"),
            heartbeat={},
            next_fire_at=(
                initial_fire_time(
                    payload.schedule,
                    payload.timezone,
                    now=reference,
                )
                if payload.enabled
                else None
            ),
            version=1,
            created_at=reference,
            updated_at=reference,
        )
        self.session.add(job)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return job

    async def get(
        self,
        job_id: str,
        *,
        include_deleted: bool = False,
    ) -> ScheduledJobRecord | None:
        query = select(ScheduledJobRecord).where(ScheduledJobRecord.id == job_id)
        if not include_deleted:
            query = query.where(ScheduledJobRecord.deleted_at.is_(None))
        return (await self.session.execute(query)).scalar_one_or_none()

    async def require(self, job_id: str) -> ScheduledJobRecord:
        job = await self.get(job_id)
        if job is None:
            raise ScheduleNotFoundError(job_id)
        return job

    async def list(
        self,
        *,
        include_disabled: bool = True,
        target_task_id: str | None = None,
        kind: ScheduledJobKind | None = None,
        limit: int = 100,
    ) -> list[ScheduledJobRecord]:
        query = select(ScheduledJobRecord).where(
            ScheduledJobRecord.deleted_at.is_(None)
        )
        if not include_disabled:
            query = query.where(ScheduledJobRecord.enabled.is_(True))
        if target_task_id is not None:
            query = query.where(ScheduledJobRecord.target_task_id == target_task_id)
        if kind is not None:
            query = query.where(ScheduledJobRecord.kind == kind.value)
        query = query.order_by(
            ScheduledJobRecord.next_fire_at.asc().nullslast(),
            ScheduledJobRecord.created_at.desc(),
        ).limit(limit)
        return list((await self.session.scalars(query)).all())

    async def update(
        self,
        job_id: str,
        payload: ScheduledJobUpdate,
        *,
        now: datetime | None = None,
    ) -> ScheduledJobRecord:
        current = await self.require(job_id)
        self._ensure_user_managed(current)
        reference = (now or utc_now()).astimezone(timezone.utc)
        values = payload.model_dump(exclude_unset=True)
        values.pop("version")

        schedule = payload.schedule or self._schedule_from_record(current)
        timezone_name = payload.timezone or current.timezone
        recalculates_fire = payload.schedule is not None or payload.timezone is not None
        mapped: dict = {"updated_at": reference, "version": payload.version + 1}
        for key, value in values.items():
            if key == "schedule":
                mapped["schedule_type"] = value.type.value
                mapped["schedule"] = value.model_dump(
                    mode="json", exclude_none=True
                )
            elif key in {"misfire_policy", "overlap_policy"}:
                mapped[key] = value.value
            elif key == "execution":
                mapped[key] = value.model_dump(mode="json")
            else:
                mapped[key] = value
        if current.enabled and recalculates_fire:
            mapped["next_fire_at"] = initial_fire_time(
                schedule,
                timezone_name,
                now=reference,
            )

        result = await self.session.execute(
            update(ScheduledJobRecord)
            .where(
                ScheduledJobRecord.id == job_id,
                ScheduledJobRecord.deleted_at.is_(None),
                ScheduledJobRecord.system_managed.is_(False),
                ScheduledJobRecord.version == payload.version,
            )
            .values(**mapped)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            latest = await self.get(job_id)
            if latest is not None and latest.system_managed:
                raise SystemManagedScheduleError(job_id)
            raise ScheduleVersionConflictError(job_id)
        await self.session.commit()
        return await self.require(job_id)

    async def set_enabled(
        self,
        job_id: str,
        *,
        enabled: bool,
        version: int,
        now: datetime | None = None,
    ) -> ScheduledJobRecord:
        current = await self.require(job_id)
        self._ensure_user_managed(current)
        reference = (now or utc_now()).astimezone(timezone.utc)
        next_fire_at = None
        if enabled:
            next_fire_at = initial_fire_time(
                self._schedule_from_record(current),
                current.timezone,
                now=reference,
            )
        result = await self.session.execute(
            update(ScheduledJobRecord)
            .where(
                ScheduledJobRecord.id == job_id,
                ScheduledJobRecord.deleted_at.is_(None),
                ScheduledJobRecord.system_managed.is_(False),
                ScheduledJobRecord.version == version,
            )
            .values(
                enabled=enabled,
                next_fire_at=next_fire_at,
                lease_owner=None,
                lease_expires_at=None,
                version=version + 1,
                updated_at=reference,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise ScheduleVersionConflictError(job_id)
        await self.session.commit()
        return await self.require(job_id)

    async def delete(
        self,
        job_id: str,
        *,
        version: int,
        now: datetime | None = None,
    ) -> ScheduledJobRecord:
        current = await self.require(job_id)
        self._ensure_user_managed(current)
        reference = (now or utc_now()).astimezone(timezone.utc)
        result = await self.session.execute(
            update(ScheduledJobRecord)
            .where(
                ScheduledJobRecord.id == job_id,
                ScheduledJobRecord.deleted_at.is_(None),
                ScheduledJobRecord.system_managed.is_(False),
                ScheduledJobRecord.version == version,
            )
            .values(
                enabled=False,
                next_fire_at=None,
                deleted_at=reference,
                version=version + 1,
                updated_at=reference,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise ScheduleVersionConflictError(job_id)
        await self.session.commit()
        deleted = await self.get(job_id, include_deleted=True)
        assert deleted is not None
        return deleted

    async def manual_trigger(
        self,
        job: ScheduledJobRecord,
        *,
        idempotency_key: str | None = None,
        claimed_by: str = "manual",
        now: datetime | None = None,
    ) -> ScheduledJobRunRecord:
        reference = (now or utc_now()).astimezone(timezone.utc)
        key = (
            f"manual:{job.id}:{idempotency_key}"
            if idempotency_key
            else f"manual:{job.id}:{uuid.uuid4()}"
        )
        existing = (
            await self.session.execute(
                select(ScheduledJobRunRecord).where(
                    ScheduledJobRunRecord.idempotency_key == key
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        schedule_run = ScheduledJobRunRecord(
            job_id=job.id,
            scheduled_for=reference,
            idempotency_key=key,
            trigger_type="manual",
            status="claimed",
            claimed_by=claimed_by,
            claimed_at=reference,
            created_at=reference,
            updated_at=reference,
        )
        self.session.add(schedule_run)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = (
                await self.session.execute(
                    select(ScheduledJobRunRecord).where(
                        ScheduledJobRunRecord.idempotency_key == key
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                raise
            return existing
        return schedule_run

    async def get_heartbeat(
        self,
        target_task_id: str,
    ) -> ScheduledJobRecord | None:
        return (
            await self.session.execute(
                select(ScheduledJobRecord).where(
                    ScheduledJobRecord.system_key == f"heartbeat:{target_task_id}",
                    ScheduledJobRecord.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()

    async def upsert_heartbeat(
        self,
        payload: HeartbeatConfig,
        *,
        owner_principal: str | None = None,
        now: datetime | None = None,
    ) -> ScheduledJobRecord:
        reference = (now or utc_now()).astimezone(timezone.utc)
        job = await self.get_heartbeat(payload.target_task_id)
        schedule = {
            "type": "interval",
            "interval_seconds": payload.interval_seconds,
        }
        next_fire_at = (
            initial_fire_time(
                self._schedule_from_dict(schedule),
                payload.timezone,
                now=reference,
            )
            if payload.enabled
            else None
        )
        heartbeat = {
            "active_hours": (
                payload.active_hours.model_dump(mode="json")
                if payload.active_hours
                else None
            ),
            "prompt": payload.prompt,
        }
        if job is None:
            job = ScheduledJobRecord(
                name="Heartbeat",
                kind=ScheduledJobKind.heartbeat.value,
                system_key=f"heartbeat:{payload.target_task_id}",
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
            self.session.add(job)
        else:
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
        await self.session.commit()
        return job

    async def disable_heartbeat(
        self,
        target_task_id: str,
        *,
        now: datetime | None = None,
    ) -> ScheduledJobRecord:
        job = await self.get_heartbeat(target_task_id)
        if job is None:
            raise ScheduleNotFoundError(f"heartbeat:{target_task_id}")
        reference = (now or utc_now()).astimezone(timezone.utc)
        job.enabled = False
        job.next_fire_at = None
        job.lease_owner = None
        job.lease_expires_at = None
        job.version += 1
        job.updated_at = reference
        await self.session.commit()
        return job

    @staticmethod
    def _ensure_user_managed(job: ScheduledJobRecord) -> None:
        if job.system_managed:
            raise SystemManagedScheduleError(job.id)

    @staticmethod
    def _schedule_from_record(job: ScheduledJobRecord):
        from app.schemas.schedules import ScheduleSpec

        return ScheduleSpec.model_validate(job.schedule)

    @staticmethod
    def _schedule_from_dict(value: dict):
        from app.schemas.schedules import ScheduleSpec

        return ScheduleSpec.model_validate(value)
