from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.model_base import utc_now
from app.db.models.scheduling import ScheduledJobRecord, ScheduledJobRunRecord
from app.scheduling.calculations import initial_fire_time, next_fire_time
from app.schemas.schedules import (
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


def _refresh_existing_claim(existing, claimed_by, reference):
    if existing.status != "claimed" or existing.run_id is not None:
        return None
    existing.claimed_by = claimed_by
    existing.claimed_at = reference
    existing.updated_at = reference
    return existing


def _schedule_skip_outcome(job, lateness, skipped_misfire, skipped_overlap):
    if skipped_misfire:
        return {
            "reason": "misfire_grace_exceeded",
            "lateness_seconds": lateness,
            "grace_seconds": job.misfire_grace_seconds,
        }
    if skipped_overlap:
        return {"reason": "overlap_policy", "policy": job.overlap_policy}
    return {}


class ScheduleRepository:
    ACTIVE_RUN_STATUSES = frozenset({"claimed", "running"})

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
        query = select(ScheduledJobRecord).where(ScheduledJobRecord.deleted_at.is_(None))
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
        values = {key: getattr(payload, key) for key in payload.model_fields_set}
        values.pop("version")

        schedule = payload.schedule or self._schedule_from_record(current)
        timezone_name = payload.timezone or current.timezone
        recalculates_fire = payload.schedule is not None or payload.timezone is not None
        mapped: dict = {"updated_at": reference, "version": payload.version + 1}
        for key, value in values.items():
            if key == "schedule":
                mapped["schedule_type"] = value.type.value
                mapped["schedule"] = value.model_dump(mode="json", exclude_none=True)
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
                select(ScheduledJobRunRecord).where(ScheduledJobRunRecord.idempotency_key == key)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing
        overlapping = await self._has_active_run(job.id)
        schedule_run = ScheduledJobRunRecord(
            job_id=job.id,
            scheduled_for=reference,
            idempotency_key=key,
            trigger_type="manual",
            status="skipped_overlap" if overlapping else "claimed",
            claimed_by=claimed_by,
            claimed_at=reference,
            completed_at=reference if overlapping else None,
            outcome=(
                {"reason": "overlap_policy", "policy": job.overlap_policy} if overlapping else {}
            ),
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

    async def claim_due(
        self,
        *,
        claimed_by: str,
        lease_seconds: int,
        batch_size: int,
        now: datetime | None = None,
    ) -> list[ScheduledJobRunRecord]:
        reference = (now or utc_now()).astimezone(timezone.utc)
        candidates = list(
            (
                await self.session.scalars(
                    select(ScheduledJobRecord)
                    .where(
                        ScheduledJobRecord.enabled.is_(True),
                        ScheduledJobRecord.deleted_at.is_(None),
                        ScheduledJobRecord.next_fire_at.is_not(None),
                        ScheduledJobRecord.next_fire_at <= reference,
                        or_(
                            ScheduledJobRecord.lease_expires_at.is_(None),
                            ScheduledJobRecord.lease_expires_at < reference,
                        ),
                    )
                    .order_by(ScheduledJobRecord.next_fire_at.asc())
                    .limit(batch_size)
                )
            ).all()
        )
        claimed: list[ScheduledJobRunRecord] = []
        for job in candidates:
            if schedule_run := await self._claim_candidate(
                job, claimed_by, lease_seconds, reference
            ):
                claimed.append(schedule_run)
        await self.session.commit()
        return claimed

    async def _claim_candidate(self, job, claimed_by, lease_seconds, reference):
        scheduled_for = job.next_fire_at
        if scheduled_for is None:
            return None
        if scheduled_for.tzinfo is None:
            scheduled_for = scheduled_for.replace(tzinfo=timezone.utc)
        if not await self._acquire_job_lease(job, claimed_by, lease_seconds, reference):
            return None
        next_fire = self._next_after_reference(
            self._schedule_from_record(job),
            job.timezone,
            scheduled_for=scheduled_for,
            reference=reference,
        )
        key = f"scheduled:{job.id}:{scheduled_for.isoformat()}"
        existing = await self.session.scalar(
            select(ScheduledJobRunRecord).where(ScheduledJobRunRecord.idempotency_key == key)
        )
        await self._advance_job(job.id, scheduled_for, next_fire, reference)
        if existing is not None:
            return _refresh_existing_claim(existing, claimed_by, reference)
        schedule_run = await self._new_schedule_run(job, key, scheduled_for, claimed_by, reference)
        self.session.add(schedule_run)
        return schedule_run if schedule_run.status == "claimed" else None

    async def _acquire_job_lease(self, job, claimed_by, lease_seconds, reference) -> bool:
        result = await self.session.execute(
            update(ScheduledJobRecord)
            .where(
                ScheduledJobRecord.id == job.id,
                ScheduledJobRecord.enabled.is_(True),
                ScheduledJobRecord.next_fire_at == job.next_fire_at,
                or_(
                    ScheduledJobRecord.lease_expires_at.is_(None),
                    ScheduledJobRecord.lease_expires_at < reference,
                ),
            )
            .values(
                lease_owner=claimed_by,
                lease_expires_at=reference + timedelta(seconds=lease_seconds),
                updated_at=reference,
            )
        )
        return result.rowcount == 1

    async def _advance_job(self, job_id, scheduled_for, next_fire, reference) -> None:
        await self.session.execute(
            update(ScheduledJobRecord)
            .where(ScheduledJobRecord.id == job_id)
            .values(
                enabled=next_fire is not None,
                last_fire_at=scheduled_for,
                next_fire_at=next_fire,
                lease_owner=None,
                lease_expires_at=None,
                updated_at=reference,
            )
        )

    async def _new_schedule_run(self, job, key, scheduled_for, claimed_by, reference):
        lateness = max(0.0, (reference - scheduled_for).total_seconds())
        skipped_misfire = lateness > job.misfire_grace_seconds and job.misfire_policy == "skip"
        skipped_overlap = (
            not skipped_misfire
            and job.overlap_policy == "skip"
            and await self._has_active_run(job.id)
        )
        status = (
            "skipped_misfire"
            if skipped_misfire
            else "skipped_overlap"
            if skipped_overlap
            else "claimed"
        )
        outcome = _schedule_skip_outcome(job, lateness, skipped_misfire, skipped_overlap)
        return ScheduledJobRunRecord(
            job_id=job.id,
            scheduled_for=scheduled_for,
            idempotency_key=key,
            trigger_type="scheduled",
            status=status,
            claimed_by=claimed_by,
            claimed_at=reference,
            completed_at=reference if status != "claimed" else None,
            outcome=outcome,
            created_at=reference,
            updated_at=reference,
        )

    async def recover_claimed(
        self,
        *,
        claimed_by: str,
        stale_after_seconds: int,
        batch_size: int,
        now: datetime | None = None,
    ) -> list[ScheduledJobRunRecord]:
        reference = (now or utc_now()).astimezone(timezone.utc)
        stale_before = reference - timedelta(seconds=stale_after_seconds)
        candidates = list(
            (
                await self.session.scalars(
                    select(ScheduledJobRunRecord)
                    .where(
                        ScheduledJobRunRecord.status == "claimed",
                        ScheduledJobRunRecord.run_id.is_(None),
                        ScheduledJobRunRecord.claimed_at <= stale_before,
                    )
                    .order_by(ScheduledJobRunRecord.claimed_at.asc())
                    .limit(batch_size)
                )
            ).all()
        )
        recovered: list[ScheduledJobRunRecord] = []
        for schedule_run in candidates:
            result = await self.session.execute(
                update(ScheduledJobRunRecord)
                .where(
                    ScheduledJobRunRecord.id == schedule_run.id,
                    ScheduledJobRunRecord.status == "claimed",
                    ScheduledJobRunRecord.run_id.is_(None),
                    ScheduledJobRunRecord.claimed_at == schedule_run.claimed_at,
                )
                .values(
                    claimed_by=claimed_by,
                    claimed_at=reference,
                    updated_at=reference,
                )
            )
            if result.rowcount == 1:
                recovered.append(schedule_run)
        await self.session.commit()
        return recovered

    async def list_runs(
        self,
        job_id: str,
        *,
        limit: int = 50,
    ) -> list[ScheduledJobRunRecord]:
        await self.require(job_id)
        query = (
            select(ScheduledJobRunRecord)
            .where(ScheduledJobRunRecord.job_id == job_id)
            .order_by(
                ScheduledJobRunRecord.created_at.desc(),
                ScheduledJobRunRecord.id.desc(),
            )
            .limit(limit)
        )
        return list((await self.session.scalars(query)).all())

    async def cleanup_runs(
        self,
        *,
        retention_days: int,
        now: datetime | None = None,
    ) -> int:
        reference = (now or utc_now()).astimezone(timezone.utc)
        cutoff = reference - timedelta(days=retention_days)
        result = await self.session.execute(
            delete(ScheduledJobRunRecord).where(
                ScheduledJobRunRecord.completed_at.is_not(None),
                ScheduledJobRunRecord.completed_at < cutoff,
            )
        )
        await self.session.commit()
        return int(result.rowcount or 0)

    async def _has_active_run(self, job_id: str) -> bool:
        return bool(
            await self.session.scalar(
                select(ScheduledJobRunRecord.id)
                .where(
                    ScheduledJobRunRecord.job_id == job_id,
                    ScheduledJobRunRecord.status.in_(self.ACTIVE_RUN_STATUSES),
                )
                .limit(1)
            )
        )

    @staticmethod
    def _next_after_reference(
        schedule,
        timezone_name: str,
        *,
        scheduled_for: datetime,
        reference: datetime,
    ) -> datetime | None:
        cursor = scheduled_for
        while True:
            candidate = next_fire_time(schedule, timezone_name, after=cursor)
            if candidate is None or candidate > reference:
                return candidate
            cursor = candidate

    @staticmethod
    def _ensure_user_managed(job: ScheduledJobRecord) -> None:
        if job.system_managed:
            raise SystemManagedScheduleError(job.id)

    @staticmethod
    def _schedule_from_record(job: ScheduledJobRecord):
        from app.schemas.schedules import ScheduleSpec

        return ScheduleSpec.model_validate(job.schedule)
