from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    MemoryAuditRecord,
    MemoryConsolidationJobRecord,
    MemoryLinkRecord,
    MemoryRecord,
    MemorySourceRecord,
    utc_now,
    uuid_str,
)
from app.memory.consolidation import (
    ConsolidationConflictError,
    ConsolidationInputManifest,
    ConsolidationProposal,
    ConsolidationValidationError,
    FrozenMemoryInput,
    canonical_digest,
    validate_proposal,
)
from app.memory.domain import MemoryNamespaceType, MemoryStatus

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
        raise ConsolidationValidationError(
            "Consolidation namespace identity must be non-empty"
        )
    if len(normalized_id) > 120:
        raise ConsolidationValidationError(
            "Consolidation namespace identity exceeds 120 characters"
        )
    return normalized_type, normalized_id


def _idempotency_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ConsolidationValidationError(
            "Consolidation idempotency key must be non-empty"
        )
    if len(normalized) > 160:
        raise ConsolidationValidationError(
            "Consolidation idempotency key exceeds 160 characters"
        )
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
            raise ConsolidationValidationError(
                f"Memory consolidation job not found: {job_id}"
            )
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
            normalized_type, normalized_id = _validate_namespace(
                namespace_type, namespace_id
            )
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
            query = query.where(
                MemoryConsolidationJobRecord.status == normalized_status
            )
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
        normalized_type, normalized_id = _validate_namespace(
            namespace_type, namespace_id
        )
        normalized_key = _idempotency_key(idempotency_key)
        existing = await self.session.scalar(
            select(MemoryConsolidationJobRecord).where(
                MemoryConsolidationJobRecord.idempotency_key == normalized_key
            )
        )
        if existing is not None:
            if (
                existing.namespace_type != normalized_type
                or existing.namespace_id != normalized_id
            ):
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
            raise ConsolidationValidationError(
                "Consolidation lease owner must be 1-120 characters"
            )
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
            raise ConsolidationValidationError(
                f"Unsupported proposal completion status: {status}"
            )
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
                MemoryConsolidationJobRecord.state_version
                == expected_state_version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise ConsolidationConflictError(
                "Consolidation job changed while storing its proposal"
            )
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
                MemoryConsolidationJobRecord.state_version
                == expected_state_version,
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
        normalized_type, normalized_id = _validate_namespace(
            namespace_type, namespace_id
        )
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

    async def publish(
        self,
        job_id: str,
        *,
        expected_state_version: int,
        actor: str | None,
        reason: str | None,
    ) -> MemoryConsolidationJobRecord:
        try:
            job = await self.require(job_id, refresh=True)
            if job.status != "proposed":
                raise ConsolidationValidationError(
                    "Only proposed consolidation jobs can be published"
                )
            if job.state_version != expected_state_version:
                raise ConsolidationConflictError(
                    "Consolidation job state version changed"
                )
            manifest = ConsolidationInputManifest.from_dict(job.input_manifest)
            proposal = ConsolidationProposal.from_dict(job.proposal)
            stored_validation = dict(job.validation or {})
            if not stored_validation.get("valid"):
                raise ConsolidationValidationError(
                    "An invalid consolidation proposal cannot be published"
                )

            source_records = await self._require_unchanged_inputs(manifest)
            validation = validate_proposal(manifest, proposal)
            if not validation.valid:
                raise ConsolidationValidationError(
                    "Consolidation proposal failed publication validation"
                )

            now = utc_now()
            output_results: list[dict[str, Any]] = []
            replacement_results: list[dict[str, Any]] = []
            source_by_id = {record.id: record for record in source_records}
            for operation in proposal.operations:
                source_memories = [
                    source_by_id[source_id]
                    for source_id in operation.source_memory_ids
                ]
                replacement_memories = [
                    source_by_id[memory_id]
                    for memory_id in operation.replace_memory_ids
                ]
                next_version = (
                    await self.session.scalar(
                        select(func.coalesce(func.max(MemoryRecord.version), 0)).where(
                            MemoryRecord.namespace_type == manifest.namespace_type,
                            MemoryRecord.namespace_id == manifest.namespace_id,
                            MemoryRecord.memory_key == operation.memory_key,
                        )
                    )
                ) + 1
                run_ids = {
                    memory.run_id for memory in source_memories if memory.run_id
                }
                run_id = next(iter(run_ids)) if len(run_ids) == 1 else None
                output_id = uuid_str()
                output = MemoryRecord(
                    id=output_id,
                    run_id=run_id,
                    workspace_id=(
                        manifest.namespace_id
                        if manifest.namespace_type == "workspace"
                        else None
                    ),
                    created_by=(
                        manifest.namespace_id
                        if manifest.namespace_type == "user"
                        else actor
                    ),
                    memory_key=operation.memory_key,
                    namespace_type=manifest.namespace_type,
                    namespace_id=manifest.namespace_id,
                    scope=operation.scope,
                    kind=operation.kind,
                    status=MemoryStatus.active.value,
                    version=next_version,
                    state_version=1,
                    content=operation.content,
                    structured_data=operation.structured_data,
                    provenance={
                        "consolidation_job_id": job.id,
                        "input_hash": manifest.input_hash,
                        "proposal_hash": proposal.proposal_hash,
                        "source_memory_ids": list(operation.source_memory_ids),
                    },
                    confidence=operation.confidence,
                    importance=operation.importance,
                    utility_score=(
                        sum(memory.utility_score for memory in source_memories)
                        / len(source_memories)
                    ),
                    observed_at=max(
                        memory.observed_at for memory in source_memories
                    ),
                    valid_from=now,
                    consolidation_generation=job.generation,
                    created_at=now,
                    updated_at=now,
                )
                self.session.add(output)
                copied_sources: set[tuple[str, str]] = set()
                for source_memory in source_memories:
                    for source in source_memory.sources:
                        identity = (source.source_kind, source.source_ref)
                        if (
                            identity in copied_sources
                            or not source.accessible
                            or source.revoked_at is not None
                        ):
                            continue
                        copied_sources.add(identity)
                        self.session.add(
                            MemorySourceRecord(
                                memory_id=output_id,
                                source_kind=source.source_kind,
                                source_ref=source.source_ref,
                                source_hash=source.source_hash,
                                run_id=source.run_id,
                                turn_id=source.turn_id,
                                tool_call_id=source.tool_call_id,
                                artifact_id=source.artifact_id,
                                source_data=source.source_data,
                                accessible=True,
                                created_at=now,
                            )
                        )
                    relation = (
                        "supersedes"
                        if source_memory.id in operation.replace_memory_ids
                        else "derived_from"
                    )
                    self.session.add(
                        MemoryLinkRecord(
                            source_memory_id=output_id,
                            target_memory_id=source_memory.id,
                            relation=relation,
                            link_data={
                                "consolidation_job_id": job.id,
                                "operation_id": operation.operation_id,
                            },
                            created_at=now,
                        )
                    )
                self.session.add(
                    MemoryAuditRecord(
                        memory_id=output_id,
                        event_type="consolidation_published",
                        actor=actor,
                        reason=reason,
                        payload={
                            "job_id": job.id,
                            "generation": job.generation,
                            "operation_id": operation.operation_id,
                            "source_memory_ids": list(
                                operation.source_memory_ids
                            ),
                        },
                        created_at=now,
                    )
                )
                output_results.append(
                    {
                        "memory_id": output_id,
                        "state_version": 1,
                        "operation_id": operation.operation_id,
                    }
                )
                for replacement in replacement_memories:
                    expected = replacement.state_version
                    result = await self.session.execute(
                        update(MemoryRecord)
                        .where(
                            MemoryRecord.id == replacement.id,
                            MemoryRecord.status == MemoryStatus.active.value,
                            MemoryRecord.state_version == expected,
                        )
                        .values(
                            status=MemoryStatus.superseded.value,
                            state_version=expected + 1,
                            valid_to=now,
                            updated_at=now,
                        )
                    )
                    if result.rowcount != 1:
                        raise ConsolidationConflictError(
                            f"Memory changed during publication: {replacement.id}"
                        )
                    self.session.add(
                        MemoryAuditRecord(
                            memory_id=replacement.id,
                            event_type="consolidation_superseded",
                            actor=actor,
                            reason=reason,
                            payload={
                                "job_id": job.id,
                                "generation": job.generation,
                                "replacement_id": output_id,
                                "state_version_before": expected,
                            },
                            created_at=now,
                        )
                    )
                    replacement_results.append(
                        {
                            "memory_id": replacement.id,
                            "state_version_before": expected,
                            "state_version_after": expected + 1,
                            "replacement_id": output_id,
                        }
                    )

            result = await self.session.execute(
                update(MemoryConsolidationJobRecord)
                .where(
                    MemoryConsolidationJobRecord.id == job.id,
                    MemoryConsolidationJobRecord.status == "proposed",
                    MemoryConsolidationJobRecord.state_version
                    == expected_state_version,
                )
                .values(
                    status="published",
                    state_version=expected_state_version + 1,
                    publish_result={
                        "input_hash": manifest.input_hash,
                        "proposal_hash": proposal.proposal_hash,
                        "outputs": output_results,
                        "replacements": replacement_results,
                    },
                    published_at=now,
                    completed_at=now,
                    error=None,
                )
            )
            if result.rowcount != 1:
                raise ConsolidationConflictError(
                    "Consolidation job changed during publication"
                )
            await self.session.commit()
            return await self.require(job.id, refresh=True)
        except (ConsolidationConflictError, IntegrityError) as exc:
            await self.session.rollback()
            await self._record_publish_conflict(
                job_id,
                expected_state_version=expected_state_version,
                message=str(exc),
            )
            if isinstance(exc, ConsolidationConflictError):
                raise
            raise ConsolidationConflictError(
                "Consolidation publication conflicted with current Memory"
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

    async def rollback_published(
        self,
        job_id: str,
        *,
        expected_state_version: int,
        actor: str | None,
        reason: str | None,
    ) -> MemoryConsolidationJobRecord:
        original = await self.require(job_id, refresh=True)
        if original.status == "rolled_back":
            existing = await self.session.scalar(
                select(MemoryConsolidationJobRecord).where(
                    MemoryConsolidationJobRecord.rollback_of_id == job_id,
                    MemoryConsolidationJobRecord.status == "published",
                )
            )
            if existing is not None:
                return existing
        if original.status != "published":
            raise ConsolidationValidationError(
                "Only published consolidation jobs can be rolled back"
            )
        if original.state_version != expected_state_version:
            raise ConsolidationConflictError(
                "Consolidation job state version changed"
            )
        result_manifest = dict(original.publish_result or {})
        outputs = list(result_manifest.get("outputs") or [])
        replacements = list(result_manifest.get("replacements") or [])
        if not outputs:
            raise ConsolidationValidationError(
                "Published consolidation job has no rollback manifest"
            )
        now = utc_now()
        try:
            for item in outputs:
                memory_id = str(item.get("memory_id") or "")
                expected = int(item.get("state_version", 0))
                result = await self.session.execute(
                    update(MemoryRecord)
                    .where(
                        MemoryRecord.id == memory_id,
                        MemoryRecord.status == MemoryStatus.active.value,
                        MemoryRecord.state_version == expected,
                    )
                    .values(
                        status=MemoryStatus.revoked.value,
                        state_version=expected + 1,
                        valid_to=now,
                        revoked_at=now,
                        revoke_reason=reason or "AutoDream generation rollback",
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    raise ConsolidationConflictError(
                        f"Published Memory changed before rollback: {memory_id}"
                    )
                self.session.add(
                    MemoryAuditRecord(
                        memory_id=memory_id,
                        event_type="consolidation_rolled_back",
                        actor=actor,
                        reason=reason,
                        payload={"job_id": original.id},
                        created_at=now,
                    )
                )
            restored_ids: set[str] = set()
            for item in replacements:
                memory_id = str(item.get("memory_id") or "")
                if memory_id in restored_ids:
                    continue
                restored_ids.add(memory_id)
                accessible_sources = await self.session.scalar(
                    select(func.count(MemorySourceRecord.id)).where(
                        MemorySourceRecord.memory_id == memory_id,
                        MemorySourceRecord.accessible.is_(True),
                        MemorySourceRecord.revoked_at.is_(None),
                    )
                )
                if not accessible_sources:
                    raise ConsolidationConflictError(
                        "Superseded Memory lost its supporting source before "
                        f"rollback: {memory_id}"
                    )
                expected = int(item.get("state_version_after", 0))
                result = await self.session.execute(
                    update(MemoryRecord)
                    .where(
                        MemoryRecord.id == memory_id,
                        MemoryRecord.status == MemoryStatus.superseded.value,
                        MemoryRecord.state_version == expected,
                    )
                    .values(
                        status=MemoryStatus.active.value,
                        state_version=expected + 1,
                        valid_to=None,
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    raise ConsolidationConflictError(
                        f"Superseded Memory changed before rollback: {memory_id}"
                    )
                self.session.add(
                    MemoryAuditRecord(
                        memory_id=memory_id,
                        event_type="consolidation_restored",
                        actor=actor,
                        reason=reason,
                        payload={"job_id": original.id},
                        created_at=now,
                    )
                )

            rollback_id = uuid_str()
            rollback_key = _idempotency_key(f"rollback:{original.id}")
            rollback_job = MemoryConsolidationJobRecord(
                id=rollback_id,
                namespace_type=original.namespace_type,
                namespace_id=original.namespace_id,
                status="published",
                state_version=1,
                generation=original.generation + 1,
                idempotency_key=rollback_key,
                input_hash=original.input_hash,
                input_manifest=original.input_manifest,
                proposal=original.proposal,
                validation={
                    "valid": True,
                    "operation": "rollback",
                    "original_job_id": original.id,
                },
                profile_snapshot=original.profile_snapshot,
                model_usage={"attempts": 0, "calls": 0, "provider": "rollback"},
                publish_result={
                    "rolled_back_job_id": original.id,
                    "revoked_output_ids": [
                        str(item.get("memory_id")) for item in outputs
                    ],
                    "restored_memory_ids": sorted(restored_ids),
                },
                rollback_of_id=original.id,
                created_at=now,
                started_at=now,
                completed_at=now,
                published_at=now,
            )
            self.session.add(rollback_job)
            result = await self.session.execute(
                update(MemoryConsolidationJobRecord)
                .where(
                    MemoryConsolidationJobRecord.id == original.id,
                    MemoryConsolidationJobRecord.status == "published",
                    MemoryConsolidationJobRecord.state_version
                    == expected_state_version,
                )
                .values(
                    status="rolled_back",
                    state_version=expected_state_version + 1,
                    error=None,
                    completed_at=now,
                )
            )
            if result.rowcount != 1:
                raise ConsolidationConflictError(
                    "Consolidation job changed during rollback"
                )
            await self.session.commit()
            return await self.require(rollback_id, refresh=True)
        except IntegrityError as exc:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(MemoryConsolidationJobRecord).where(
                    MemoryConsolidationJobRecord.rollback_of_id == job_id,
                    MemoryConsolidationJobRecord.status == "published",
                )
            )
            if existing is not None:
                return existing
            raise ConsolidationConflictError(
                "Consolidation rollback conflicted with current state"
            ) from exc
        except Exception:
            await self.session.rollback()
            raise

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
            raise ConsolidationValidationError(
                "AutoDream namespace batch must be between 1 and 32"
            )
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
            raise ConsolidationValidationError(
                "AutoDream worker batch must be between 1 and 32"
            )
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
                raise ConsolidationConflictError(
                    f"Frozen Memory no longer exists: {frozen.id}"
                )
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
                MemoryConsolidationJobRecord.state_version
                == expected_state_version,
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
    return created_at is None or created_at + timedelta(
        seconds=cooldown_seconds
    ) <= _as_utc(now)


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


def stable_source_digest(records: Iterable[MemoryRecord]) -> str:
    return canonical_digest(
        [
            {
                "id": record.id,
                "version": record.version,
                "state_version": record.state_version,
            }
            for record in records
        ]
    )
