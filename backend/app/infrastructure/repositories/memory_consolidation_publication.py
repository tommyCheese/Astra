from __future__ import annotations

import hashlib
from typing import Any, Never

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.application.memory.consolidation.models import (
    ConsolidationConflictError,
    ConsolidationInputManifest,
    ConsolidationOperation,
    ConsolidationProposal,
    ConsolidationValidationError,
)
from app.application.memory.consolidation.validation import validate_proposal
from app.domain.memory import MemoryStatus
from app.infrastructure.db.model_base import utc_now, uuid_str
from app.infrastructure.db.models.memory import (
    MemoryConsolidationJobRecord,
    MemoryRecord,
    MemorySourceRecord,
)
from app.infrastructure.repositories.memory_consolidation_outputs import (
    PublicationContext,
    RollbackManifest,
    copy_sources_and_create_links,
    create_output_memory,
    record_memory_audit,
    supersede_replacements,
)


def publication_idempotency_key(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class MemoryConsolidationPublicationService:
    """Atomically publish or roll back one validated consolidation proposal."""

    def __init__(self, repository: Any):
        self.repository = repository
        self.session = repository.session

    async def publish(
        self,
        job_id: str,
        *,
        expected_state_version: int,
        actor: str | None,
        reason: str | None,
    ) -> MemoryConsolidationJobRecord:
        try:
            return await self._publish_transaction(
                job_id,
                expected_state_version=expected_state_version,
                actor=actor,
                reason=reason,
            )
        except (ConsolidationConflictError, IntegrityError) as exc:
            await self._raise_publication_conflict(
                job_id, expected_state_version=expected_state_version, cause=exc
            )
        except Exception:
            await self.session.rollback()
            raise

    async def _publish_transaction(
        self,
        job_id: str,
        *,
        expected_state_version: int,
        actor: str | None,
        reason: str | None,
    ) -> MemoryConsolidationJobRecord:
        context = await self._prepare_publication(job_id, expected_state_version)
        outputs: list[dict[str, Any]] = []
        replacements: list[dict[str, Any]] = []
        for operation in context.proposal.operations:
            output, superseded = await self._publish_operation(
                context, operation, actor=actor, reason=reason
            )
            outputs.append(output)
            replacements.extend(superseded)
        await self._mark_job_published(
            context,
            expected_state_version=expected_state_version,
            outputs=outputs,
            replacements=replacements,
        )
        await self.session.commit()
        return await self.repository.require(context.job.id, refresh=True)

    async def _prepare_publication(
        self, job_id: str, expected_state_version: int
    ) -> PublicationContext:
        job = await self.repository.require(job_id, refresh=True)
        self._require_publishable_job(job, expected_state_version)
        manifest = ConsolidationInputManifest.from_dict(job.input_manifest)
        proposal = ConsolidationProposal.from_dict(job.proposal)
        source_records = await self.repository._require_unchanged_inputs(manifest)
        if not validate_proposal(manifest, proposal).valid:
            raise ConsolidationValidationError(
                "Consolidation proposal failed publication validation"
            )
        return PublicationContext(
            job=job,
            manifest=manifest,
            proposal=proposal,
            source_by_id={record.id: record for record in source_records},
            published_at=utc_now(),
        )

    @staticmethod
    def _require_publishable_job(
        job: MemoryConsolidationJobRecord, expected_state_version: int
    ) -> None:
        if job.status != "proposed":
            raise ConsolidationValidationError("Only proposed consolidation jobs can be published")
        if job.state_version != expected_state_version:
            raise ConsolidationConflictError("Consolidation job state version changed")
        if not dict(job.validation or {}).get("valid"):
            raise ConsolidationValidationError(
                "An invalid consolidation proposal cannot be published"
            )

    async def _publish_operation(
        self,
        context: PublicationContext,
        operation: ConsolidationOperation,
        *,
        actor: str | None,
        reason: str | None,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        source_memories = [
            context.source_by_id[memory_id] for memory_id in operation.source_memory_ids
        ]
        output = await create_output_memory(
            self.session, context, operation, source_memories, actor
        )
        copy_sources_and_create_links(
            self.session,
            operation,
            source_memories,
            output.id,
            job_id=context.job.id,
            published_at=context.published_at,
        )
        record_memory_audit(
            self.session,
            output.id,
            "consolidation_published",
            actor,
            reason,
            {
                "job_id": context.job.id,
                "generation": context.job.generation,
                "operation_id": operation.operation_id,
                "source_memory_ids": list(operation.source_memory_ids),
            },
            context.published_at,
        )
        superseded = await supersede_replacements(
            self.session,
            context,
            operation,
            output.id,
            actor=actor,
            reason=reason,
        )
        return {
            "memory_id": output.id,
            "state_version": 1,
            "operation_id": operation.operation_id,
        }, superseded

    async def _mark_job_published(
        self,
        context: PublicationContext,
        *,
        expected_state_version: int,
        outputs: list[dict[str, Any]],
        replacements: list[dict[str, Any]],
    ) -> None:
        result = await self.session.execute(
            update(MemoryConsolidationJobRecord)
            .where(
                MemoryConsolidationJobRecord.id == context.job.id,
                MemoryConsolidationJobRecord.status == "proposed",
                MemoryConsolidationJobRecord.state_version == expected_state_version,
            )
            .values(
                status="published",
                state_version=expected_state_version + 1,
                publish_result={
                    "input_hash": context.manifest.input_hash,
                    "proposal_hash": context.proposal.proposal_hash,
                    "outputs": outputs,
                    "replacements": replacements,
                },
                published_at=context.published_at,
                completed_at=context.published_at,
                error=None,
            )
        )
        if result.rowcount != 1:
            raise ConsolidationConflictError("Consolidation job changed during publication")

    async def _raise_publication_conflict(
        self,
        job_id: str,
        *,
        expected_state_version: int,
        cause: Exception,
    ) -> Never:
        await self.session.rollback()
        await self.repository._record_publish_conflict(
            job_id,
            expected_state_version=expected_state_version,
            message=str(cause),
        )
        if isinstance(cause, ConsolidationConflictError):
            raise cause
        raise ConsolidationConflictError(
            "Consolidation publication conflicted with current Memory"
        ) from cause

    async def rollback_published(
        self,
        job_id: str,
        *,
        expected_state_version: int,
        actor: str | None,
        reason: str | None,
    ) -> MemoryConsolidationJobRecord:
        existing, manifest = await self._prepare_rollback(job_id, expected_state_version)
        if existing is not None:
            return existing
        assert manifest is not None
        try:
            return await self._rollback_transaction(
                manifest,
                expected_state_version=expected_state_version,
                actor=actor,
                reason=reason,
            )
        except IntegrityError as exc:
            return await self._resolve_rollback_conflict(job_id, exc)
        except Exception:
            await self.session.rollback()
            raise

    async def _prepare_rollback(
        self, job_id: str, expected_state_version: int
    ) -> tuple[MemoryConsolidationJobRecord | None, RollbackManifest | None]:
        original = await self.repository.require(job_id, refresh=True)
        if original.status == "rolled_back":
            existing = await self._find_rollback_job(job_id)
            if existing is not None:
                return existing, None
        if original.status != "published":
            raise ConsolidationValidationError(
                "Only published consolidation jobs can be rolled back"
            )
        if original.state_version != expected_state_version:
            raise ConsolidationConflictError("Consolidation job state version changed")
        publication = dict(original.publish_result or {})
        outputs = list(publication.get("outputs") or [])
        if not outputs:
            raise ConsolidationValidationError(
                "Published consolidation job has no rollback manifest"
            )
        return None, RollbackManifest(
            original=original,
            outputs=outputs,
            replacements=list(publication.get("replacements") or []),
            rolled_back_at=utc_now(),
        )

    async def _rollback_transaction(
        self,
        manifest: RollbackManifest,
        *,
        expected_state_version: int,
        actor: str | None,
        reason: str | None,
    ) -> MemoryConsolidationJobRecord:
        await self._revoke_outputs(manifest, actor=actor, reason=reason)
        restored_ids = await self._restore_replacements(manifest, actor=actor, reason=reason)
        rollback_job = self._create_rollback_job(manifest, restored_ids)
        self.session.add(rollback_job)
        await self._mark_original_rolled_back(manifest, expected_state_version)
        await self.session.commit()
        return await self.repository.require(rollback_job.id, refresh=True)

    async def _revoke_outputs(
        self,
        manifest: RollbackManifest,
        *,
        actor: str | None,
        reason: str | None,
    ) -> None:
        for item in manifest.outputs:
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
                    valid_to=manifest.rolled_back_at,
                    revoked_at=manifest.rolled_back_at,
                    revoke_reason=reason or "AutoDream generation rollback",
                    updated_at=manifest.rolled_back_at,
                )
            )
            if result.rowcount != 1:
                raise ConsolidationConflictError(
                    f"Published Memory changed before rollback: {memory_id}"
                )
            record_memory_audit(
                self.session,
                memory_id,
                "consolidation_rolled_back",
                actor,
                reason,
                {"job_id": manifest.original.id},
                manifest.rolled_back_at,
            )

    async def _restore_replacements(
        self,
        manifest: RollbackManifest,
        *,
        actor: str | None,
        reason: str | None,
    ) -> set[str]:
        restored_ids: set[str] = set()
        for item in manifest.replacements:
            memory_id = str(item.get("memory_id") or "")
            if memory_id in restored_ids:
                continue
            await self._restore_replacement(manifest, item, memory_id, actor=actor, reason=reason)
            restored_ids.add(memory_id)
        return restored_ids

    async def _restore_replacement(
        self,
        manifest: RollbackManifest,
        item: dict[str, Any],
        memory_id: str,
        *,
        actor: str | None,
        reason: str | None,
    ) -> None:
        if not await self._has_accessible_source(memory_id):
            raise ConsolidationConflictError(
                f"Superseded Memory lost its supporting source before rollback: {memory_id}"
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
                updated_at=manifest.rolled_back_at,
            )
        )
        if result.rowcount != 1:
            raise ConsolidationConflictError(
                f"Superseded Memory changed before rollback: {memory_id}"
            )
        record_memory_audit(
            self.session,
            memory_id,
            "consolidation_restored",
            actor,
            reason,
            {"job_id": manifest.original.id},
            manifest.rolled_back_at,
        )

    async def _has_accessible_source(self, memory_id: str) -> bool:
        count = await self.session.scalar(
            select(func.count(MemorySourceRecord.id)).where(
                MemorySourceRecord.memory_id == memory_id,
                MemorySourceRecord.accessible.is_(True),
                MemorySourceRecord.revoked_at.is_(None),
            )
        )
        return bool(count)

    @staticmethod
    def _create_rollback_job(
        manifest: RollbackManifest, restored_ids: set[str]
    ) -> MemoryConsolidationJobRecord:
        original = manifest.original
        return MemoryConsolidationJobRecord(
            id=uuid_str(),
            namespace_type=original.namespace_type,
            namespace_id=original.namespace_id,
            status="published",
            state_version=1,
            generation=original.generation + 1,
            idempotency_key=publication_idempotency_key(f"rollback:{original.id}"),
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
                "revoked_output_ids": [str(item.get("memory_id")) for item in manifest.outputs],
                "restored_memory_ids": sorted(restored_ids),
            },
            rollback_of_id=original.id,
            created_at=manifest.rolled_back_at,
            started_at=manifest.rolled_back_at,
            completed_at=manifest.rolled_back_at,
            published_at=manifest.rolled_back_at,
        )

    async def _mark_original_rolled_back(
        self, manifest: RollbackManifest, expected_state_version: int
    ) -> None:
        result = await self.session.execute(
            update(MemoryConsolidationJobRecord)
            .where(
                MemoryConsolidationJobRecord.id == manifest.original.id,
                MemoryConsolidationJobRecord.status == "published",
                MemoryConsolidationJobRecord.state_version == expected_state_version,
            )
            .values(
                status="rolled_back",
                state_version=expected_state_version + 1,
                error=None,
                completed_at=manifest.rolled_back_at,
            )
        )
        if result.rowcount != 1:
            raise ConsolidationConflictError("Consolidation job changed during rollback")

    async def _resolve_rollback_conflict(
        self, job_id: str, cause: IntegrityError
    ) -> MemoryConsolidationJobRecord:
        await self.session.rollback()
        existing = await self._find_rollback_job(job_id)
        if existing is not None:
            return existing
        raise ConsolidationConflictError(
            "Consolidation rollback conflicted with current state"
        ) from cause

    async def _find_rollback_job(self, job_id: str) -> MemoryConsolidationJobRecord | None:
        return await self.session.scalar(
            select(MemoryConsolidationJobRecord).where(
                MemoryConsolidationJobRecord.rollback_of_id == job_id,
                MemoryConsolidationJobRecord.status == "published",
            )
        )
