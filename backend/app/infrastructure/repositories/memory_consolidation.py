from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.memory.consolidation.models import (
    ConsolidationConflictError,
    ConsolidationInputManifest,
    ConsolidationProposal,
    ConsolidationValidationError,
    FrozenMemoryInput,
    canonical_digest,
)
from app.domain.memory import MemoryNamespaceType, MemoryStatus
from app.infrastructure.db.model_base import utc_now, uuid_str
from app.infrastructure.db.models.memory import (
    MemoryConsolidationJobRecord,
    MemoryRecord,
    MemorySourceRecord,
)

ACTIVE_JOB_STATUSES = frozenset({"queued", "running", "proposed"})
TERMINAL_JOB_STATUSES = frozenset(
    {
        "insufficient_input",
        "failed",
        "conflict",
        "published",
        "rolled_back",
    }
)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _validate_namespace(namespace_type: str, namespace_id: str) -> tuple[str, str]:
    try:
        normalized_type = MemoryNamespaceType(str(namespace_type).strip()).value
    except ValueError as exc:
        raise ConsolidationValidationError(
            f"Unsupported consolidation namespace: {namespace_type}"
        ) from exc
    normalized_id = str(namespace_id or "").strip()
    if not normalized_id:
        raise ConsolidationValidationError("Consolidation namespace identity must be non-empty")
    if len(normalized_id) > 120:
        raise ConsolidationValidationError(
            "Consolidation namespace identity exceeds 120 characters"
        )
    return normalized_type, normalized_id


def _idempotency_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ConsolidationValidationError("Consolidation idempotency key must be non-empty")
    if len(normalized) > 160:
        raise ConsolidationValidationError("Consolidation idempotency key exceeds 160 characters")
    return normalized


class MemoryConsolidationRepository:
    """Persistence boundary for reviewable, atomic Memory generations."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def require(
        self,
        job_id: str,
        *,
        refresh: bool = False,
    ) -> MemoryConsolidationJobRecord:
        if refresh:
            job = await self.session.scalar(
                select(MemoryConsolidationJobRecord)
                .where(MemoryConsolidationJobRecord.id == job_id)
                .execution_options(populate_existing=True)
            )
        else:
            job = await self.session.get(MemoryConsolidationJobRecord, job_id)
        if job is None:
            raise ConsolidationValidationError(f"Memory consolidation job not found: {job_id}")
        return job

    async def list_jobs(
        self,
        *,
        namespace_type: str | None = None,
        namespace_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[MemoryConsolidationJobRecord]:
        if not 1 <= limit <= 200:
            raise ConsolidationValidationError(
                "Consolidation job list limit must be between 1 and 200"
            )
        query = select(MemoryConsolidationJobRecord)
        if namespace_type is not None or namespace_id is not None:
            if namespace_type is None or namespace_id is None:
                raise ConsolidationValidationError(
                    "Both namespace_type and namespace_id are required"
                )
            normalized_type, normalized_id = _validate_namespace(namespace_type, namespace_id)
            query = query.where(
                MemoryConsolidationJobRecord.namespace_type == normalized_type,
                MemoryConsolidationJobRecord.namespace_id == normalized_id,
            )
        if status is not None:
            normalized_status = str(status).strip()
            if normalized_status not in ACTIVE_JOB_STATUSES | TERMINAL_JOB_STATUSES:
                raise ConsolidationValidationError(
                    f"Unsupported consolidation job status: {status}"
                )
            query = query.where(MemoryConsolidationJobRecord.status == normalized_status)
        query = query.order_by(
            MemoryConsolidationJobRecord.created_at.desc(),
            MemoryConsolidationJobRecord.id,
        ).limit(limit)
        return list((await self.session.scalars(query)).all())

    async def create_job(
        self,
        *,
        namespace_type: str,
        namespace_id: str,
        idempotency_key: str,
        rollback_of_id: str | None = None,
    ) -> MemoryConsolidationJobRecord:
        normalized_type, normalized_id = _validate_namespace(namespace_type, namespace_id)
        normalized_key = _idempotency_key(idempotency_key)
        existing = await self.session.scalar(
            select(MemoryConsolidationJobRecord).where(
                MemoryConsolidationJobRecord.idempotency_key == normalized_key
            )
        )
        if existing is not None:
            if existing.namespace_type != normalized_type or existing.namespace_id != normalized_id:
                raise ConsolidationConflictError(
                    "Consolidation idempotency key belongs to another namespace"
                )
            return existing

        active = await self.session.scalar(
            select(MemoryConsolidationJobRecord)
            .where(
                MemoryConsolidationJobRecord.namespace_type == normalized_type,
                MemoryConsolidationJobRecord.namespace_id == normalized_id,
                MemoryConsolidationJobRecord.status.in_(ACTIVE_JOB_STATUSES),
            )
            .order_by(MemoryConsolidationJobRecord.created_at.desc())
            .limit(1)
        )
        if active is not None:
            return active

        generation = (
            await self.session.scalar(
                select(
                    func.coalesce(
                        func.max(MemoryConsolidationJobRecord.generation),
                        0,
                    )
                ).where(
                    MemoryConsolidationJobRecord.namespace_type == normalized_type,
                    MemoryConsolidationJobRecord.namespace_id == normalized_id,
                )
            )
        ) + 1
        job = MemoryConsolidationJobRecord(
            namespace_type=normalized_type,
            namespace_id=normalized_id,
            status="queued",
            state_version=1,
            generation=generation,
            idempotency_key=normalized_key,
            input_manifest={},
            proposal={},
            validation={},
            profile_snapshot={},
            model_usage={"attempts": 0, "calls": 0},
            publish_result={},
            rollback_of_id=rollback_of_id,
            created_at=utc_now(),
        )
        self.session.add(job)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(MemoryConsolidationJobRecord).where(
                    MemoryConsolidationJobRecord.idempotency_key == normalized_key
                )
            )
            if existing is None:
                raise ConsolidationConflictError(
                    "A consolidation job was created concurrently"
                ) from exc
            return existing
        return job

    async def claim(
        self,
        job_id: str,
        *,
        owner: str,
        lease_seconds: int,
    ) -> MemoryConsolidationJobRecord | None:
        normalized_owner = str(owner or "").strip()
        if not normalized_owner or len(normalized_owner) > 120:
            raise ConsolidationValidationError("Consolidation lease owner must be 1-120 characters")
        if not 30 <= lease_seconds <= 3_600:
            raise ConsolidationValidationError(
                "Consolidation lease must be between 30 and 3600 seconds"
            )
        job = await self.require(job_id, refresh=True)
        now = utc_now()
        expired = (
            job.status == "running"
            and _as_utc(job.lease_expires_at) is not None
            and _as_utc(job.lease_expires_at) <= _as_utc(now)
        )
        if job.status != "queued" and not expired:
            return None
        expected_version = job.state_version
        usage = dict(job.model_usage or {})
        usage["attempts"] = int(usage.get("attempts", 0)) + 1
        usage.setdefault("calls", 0)
        result = await self.session.execute(
            update(MemoryConsolidationJobRecord)
            .where(
                MemoryConsolidationJobRecord.id == job_id,
                MemoryConsolidationJobRecord.state_version == expected_version,
                or_(
                    MemoryConsolidationJobRecord.status == "queued",
                    and_(
                        MemoryConsolidationJobRecord.status == "running",
                        MemoryConsolidationJobRecord.lease_expires_at <= now,
                    ),
                ),
            )
            .values(
                status="running",
                state_version=expected_version + 1,
                lease_owner=normalized_owner,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=job.started_at or now,
                completed_at=None,
                error=None,
                model_usage=usage,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return None
        await self.session.commit()
        return await self.require(job_id, refresh=True)

    async def recover_expired(self) -> int:
        now = utc_now()
        jobs = list(
            (
                await self.session.scalars(
                    select(MemoryConsolidationJobRecord).where(
                        MemoryConsolidationJobRecord.status == "running",
                        MemoryConsolidationJobRecord.lease_expires_at.is_not(None),
                        MemoryConsolidationJobRecord.lease_expires_at <= now,
                    )
                )
            ).all()
        )
        recovered = 0
        for job in jobs:
            result = await self.session.execute(
                update(MemoryConsolidationJobRecord)
                .where(
                    MemoryConsolidationJobRecord.id == job.id,
                    MemoryConsolidationJobRecord.state_version == job.state_version,
                    MemoryConsolidationJobRecord.status == "running",
                    MemoryConsolidationJobRecord.lease_expires_at <= now,
                )
                .values(
                    status="queued",
                    state_version=job.state_version + 1,
                    lease_owner=None,
                    lease_expires_at=None,
                    error={
                        "code": "interrupted",
                        "message": "Expired AutoDream lease recovered at startup",
                    },
                )
            )
            recovered += int(result.rowcount or 0)
        await self.session.commit()
        return recovered

    async def complete_proposal(
        self,
        job_id: str,
        *,
        expected_state_version: int,
        manifest: ConsolidationInputManifest | None,
        proposal: ConsolidationProposal | None,
        validation: dict[str, Any],
        profile_snapshot: dict[str, Any],
        status: str,
        model_usage: dict[str, Any],
        error: dict[str, Any] | None = None,
    ) -> MemoryConsolidationJobRecord:
        if status not in {"proposed", "insufficient_input", "failed", "conflict"}:
            raise ConsolidationValidationError(f"Unsupported proposal completion status: {status}")
        values: dict[str, Any] = {
            "status": status,
            "state_version": expected_state_version + 1,
            "input_hash": manifest.input_hash if manifest else None,
            "input_manifest": manifest.to_dict() if manifest else {},
            "proposal": proposal.to_dict() if proposal else {},
            "validation": validation,
            "profile_snapshot": profile_snapshot,
            "model_usage": model_usage,
            "error": error,
            "lease_owner": None,
            "lease_expires_at": None,
            "completed_at": utc_now(),
        }
        result = await self.session.execute(
            update(MemoryConsolidationJobRecord)
            .where(
                MemoryConsolidationJobRecord.id == job_id,
                MemoryConsolidationJobRecord.status == "running",
                MemoryConsolidationJobRecord.state_version == expected_state_version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise ConsolidationConflictError("Consolidation job changed while storing its proposal")
        await self.session.commit()
        return await self.require(job_id, refresh=True)

    async def fail_running(
        self,
        job_id: str,
        *,
        expected_state_version: int,
        code: str,
        message: str,
    ) -> MemoryConsolidationJobRecord | None:
        result = await self.session.execute(
            update(MemoryConsolidationJobRecord)
            .where(
                MemoryConsolidationJobRecord.id == job_id,
                MemoryConsolidationJobRecord.status == "running",
                MemoryConsolidationJobRecord.state_version == expected_state_version,
            )
            .values(
                status="failed",
                state_version=expected_state_version + 1,
                error={"code": str(code)[:80], "message": str(message)[:2_000]},
                lease_owner=None,
                lease_expires_at=None,
                completed_at=utc_now(),
            )
        )
        await self.session.commit()
        if result.rowcount != 1:
            return None
        return await self.require(job_id, refresh=True)

    async def eligible_memories(
        self,
        *,
        namespace_type: str,
        namespace_id: str,
        limit: int,
    ) -> list[MemoryRecord]:
        normalized_type, normalized_id = _validate_namespace(namespace_type, namespace_id)
        if not 2 <= limit <= 100:
            raise ConsolidationValidationError(
                "Consolidation input limit must be between 2 and 100"
            )
        now = utc_now()
        query = (
            select(MemoryRecord)
            .where(
                MemoryRecord.namespace_type == normalized_type,
                MemoryRecord.namespace_id == normalized_id,
                MemoryRecord.status == MemoryStatus.active.value,
                or_(MemoryRecord.expires_at.is_(None), MemoryRecord.expires_at > now),
                or_(MemoryRecord.valid_to.is_(None), MemoryRecord.valid_to > now),
                MemoryRecord.sources.any(
                    and_(
                        MemorySourceRecord.accessible.is_(True),
                        MemorySourceRecord.revoked_at.is_(None),
                    )
                ),
            )
            .options(selectinload(MemoryRecord.sources))
            .order_by(
                MemoryRecord.memory_key,
                MemoryRecord.version.desc(),
                MemoryRecord.id,
            )
            .limit(limit)
        )
        return list((await self.session.scalars(query)).all())

    async def latest_job_for_namespace(
        self,
        *,
        namespace_type: str,
        namespace_id: str,
    ) -> MemoryConsolidationJobRecord | None:
        return await self.session.scalar(
            select(MemoryConsolidationJobRecord)
            .where(
                MemoryConsolidationJobRecord.namespace_type == namespace_type,
                MemoryConsolidationJobRecord.namespace_id == namespace_id,
            )
            .order_by(MemoryConsolidationJobRecord.created_at.desc())
            .limit(1)
        )

    async def eligible_namespaces(
        self,
        *,
        minimum_count: int,
        limit: int,
    ) -> list[tuple[str, str, int]]:
        if not 2 <= minimum_count <= 100:
            raise ConsolidationValidationError(
                "AutoDream minimum candidate count must be between 2 and 100"
            )
        if not 1 <= limit <= 32:
            raise ConsolidationValidationError("AutoDream namespace batch must be between 1 and 32")
        now = utc_now()
        rows = (
            await self.session.execute(
                select(
                    MemoryRecord.namespace_type,
                    MemoryRecord.namespace_id,
                    func.count(func.distinct(MemoryRecord.id)),
                )
                .join(MemorySourceRecord)
                .where(
                    MemoryRecord.status == MemoryStatus.active.value,
                    or_(
                        MemoryRecord.expires_at.is_(None),
                        MemoryRecord.expires_at > now,
                    ),
                    or_(
                        MemoryRecord.valid_to.is_(None),
                        MemoryRecord.valid_to > now,
                    ),
                    MemorySourceRecord.accessible.is_(True),
                    MemorySourceRecord.revoked_at.is_(None),
                )
                .group_by(
                    MemoryRecord.namespace_type,
                    MemoryRecord.namespace_id,
                )
                .having(func.count(func.distinct(MemoryRecord.id)) >= minimum_count)
                .order_by(
                    MemoryRecord.namespace_type,
                    MemoryRecord.namespace_id,
                )
                .limit(limit)
            )
        ).all()
        return [(str(row[0]), str(row[1]), int(row[2])) for row in rows]

    async def candidate_fingerprint(
        self,
        *,
        namespace_type: str,
        namespace_id: str,
        limit: int,
    ) -> str:
        records = await self.eligible_memories(
            namespace_type=namespace_type,
            namespace_id=namespace_id,
            limit=limit,
        )
        return canonical_digest(
            [
                {
                    "id": record.id,
                    "version": record.version,
                    "state_version": record.state_version,
                    "status": record.status,
                }
                for record in records
            ]
        )

    async def queued_job_ids(self, *, limit: int) -> list[str]:
        if not 1 <= limit <= 32:
            raise ConsolidationValidationError("AutoDream worker batch must be between 1 and 32")
        return list(
            (
                await self.session.scalars(
                    select(MemoryConsolidationJobRecord.id)
                    .where(MemoryConsolidationJobRecord.status == "queued")
                    .order_by(
                        MemoryConsolidationJobRecord.created_at,
                        MemoryConsolidationJobRecord.id,
                    )
                    .limit(limit)
                )
            ).all()
        )

    async def _require_unchanged_inputs(
        self,
        manifest: ConsolidationInputManifest,
    ) -> list[MemoryRecord]:
        input_ids = [item.id for item in manifest.items]
        records = list(
            (
                await self.session.scalars(
                    select(MemoryRecord)
                    .where(MemoryRecord.id.in_(input_ids))
                    .options(selectinload(MemoryRecord.sources))
                )
            ).all()
        )
        by_id = {record.id: record for record in records}
        for frozen in manifest.items:
            current = by_id.get(frozen.id)
            if current is None:
                raise ConsolidationConflictError(f"Frozen Memory no longer exists: {frozen.id}")
            current_frozen = FrozenMemoryInput.from_record(current)
            if current_frozen.memory_hash != frozen.memory_hash:
                raise ConsolidationConflictError(
                    f"Frozen Memory changed before publication: {frozen.id}"
                )
        return records

    async def _record_publish_conflict(
        self,
        job_id: str,
        *,
        expected_state_version: int,
        message: str,
    ) -> None:
        await self.session.execute(
            update(MemoryConsolidationJobRecord)
            .where(
                MemoryConsolidationJobRecord.id == job_id,
                MemoryConsolidationJobRecord.status == "proposed",
                MemoryConsolidationJobRecord.state_version == expected_state_version,
            )
            .values(
                status="conflict",
                state_version=expected_state_version + 1,
                error={
                    "code": "publication_conflict",
                    "message": str(message)[:2_000],
                },
                completed_at=utc_now(),
            )
        )
        await self.session.commit()


def generated_manual_idempotency_key(
    namespace_type: str,
    namespace_id: str,
) -> str:
    payload = json.dumps(
        {
            "namespace_type": namespace_type,
            "namespace_id": namespace_id,
            "nonce": uuid_str(),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"manual:{hashlib.sha256(payload.encode()).hexdigest()}"


def scan_idempotency_key(
    namespace_type: str,
    namespace_id: str,
    fingerprint: str,
) -> str:
    digest = canonical_digest(
        {
            "namespace_type": namespace_type,
            "namespace_id": namespace_id,
            "fingerprint": fingerprint,
        }
    )
    return f"scan:{digest}"


def cooldown_elapsed(
    latest: MemoryConsolidationJobRecord | None,
    *,
    now: datetime,
    cooldown_seconds: int,
) -> bool:
    if latest is None or cooldown_seconds == 0:
        return True
    created_at = _as_utc(latest.created_at)
    return created_at is None or created_at + timedelta(seconds=cooldown_seconds) <= _as_utc(now)


def model_usage_for_job(
    job: MemoryConsolidationJobRecord,
    *,
    provider: str,
    calls: int,
) -> dict[str, Any]:
    usage = dict(job.model_usage or {})
    usage.update({"provider": provider, "calls": calls})
    return usage


def proposal_failure_payload(exc: Exception) -> dict[str, str]:
    return {
        "code": type(exc).__name__,
        "message": str(exc)[:2_000],
    }
