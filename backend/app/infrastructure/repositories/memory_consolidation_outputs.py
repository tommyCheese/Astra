from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update

from app.application.memory.consolidation.models import (
    ConsolidationConflictError,
    ConsolidationInputManifest,
    ConsolidationOperation,
    ConsolidationProposal,
)
from app.domain.memory import MemoryStatus
from app.infrastructure.db.model_base import uuid_str
from app.infrastructure.db.models.memory import (
    MemoryAuditRecord,
    MemoryConsolidationJobRecord,
    MemoryLinkRecord,
    MemorySourceRecord,
    PersistedMemoryRecord,
)


@dataclass(frozen=True)
class MemoryPublicationContext:
    job: MemoryConsolidationJobRecord
    manifest: ConsolidationInputManifest
    proposal: ConsolidationProposal
    source_by_id: dict[str, PersistedMemoryRecord]
    published_at: datetime


@dataclass(frozen=True)
class MemoryRollbackManifest:
    original: MemoryConsolidationJobRecord
    outputs: list[dict[str, Any]]
    replacements: list[dict[str, Any]]
    rolled_back_at: datetime


def record_memory_audit(
    session: Any,
    memory_id: str,
    event_type: str,
    actor: str | None,
    reason: str | None,
    payload: dict[str, Any],
    created_at: datetime,
) -> None:
    session.add(
        MemoryAuditRecord(
            memory_id=memory_id,
            event_type=event_type,
            actor=actor,
            reason=reason,
            payload=payload,
            created_at=created_at,
        )
    )


async def next_memory_version(session: Any, manifest: ConsolidationInputManifest, memory_key: str) -> int:
    current = await session.scalar(
        select(func.coalesce(func.max(PersistedMemoryRecord.version), 0)).where(
            PersistedMemoryRecord.namespace_type == manifest.namespace_type,
            PersistedMemoryRecord.namespace_id == manifest.namespace_id,
            PersistedMemoryRecord.memory_key == memory_key,
        )
    )
    return int(current) + 1


def copy_sources_and_create_links(
    session: Any,
    operation: ConsolidationOperation,
    source_memories: list[PersistedMemoryRecord],
    output_id: str,
    *,
    job_id: str,
    published_at: datetime,
) -> None:
    copied_sources: set[tuple[str, str]] = set()
    for source_memory in source_memories:
        for source in source_memory.sources:
            identity = (source.source_kind, source.source_ref)
            if identity in copied_sources or not source.accessible or source.revoked_at is not None:
                continue
            copied_sources.add(identity)
            session.add(_copy_source(source, output_id, published_at))
        session.add(
            MemoryLinkRecord(
                source_memory_id=output_id,
                target_memory_id=source_memory.id,
                relation=("supersedes" if source_memory.id in operation.replace_memory_ids else "derived_from"),
                link_data={
                    "consolidation_job_id": job_id,
                    "operation_id": operation.operation_id,
                },
                created_at=published_at,
            )
        )


def _copy_source(source: MemorySourceRecord, output_id: str, created_at: datetime) -> MemorySourceRecord:
    return MemorySourceRecord(
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
        created_at=created_at,
    )


async def create_output_memory(
    session: Any,
    context: MemoryPublicationContext,
    operation: ConsolidationOperation,
    source_memories: list[PersistedMemoryRecord],
    actor: str | None,
) -> PersistedMemoryRecord:
    version = await next_memory_version(session, context.manifest, operation.memory_key)
    run_ids = {memory.run_id for memory in source_memories if memory.run_id}
    output = PersistedMemoryRecord(
        id=uuid_str(),
        run_id=next(iter(run_ids)) if len(run_ids) == 1 else None,
        created_by=_output_creator(context, actor),
        memory_key=operation.memory_key,
        namespace_type=context.manifest.namespace_type,
        namespace_id=context.manifest.namespace_id,
        scope=operation.scope,
        kind=operation.kind,
        status=MemoryStatus.active.value,
        version=version,
        state_version=1,
        content=operation.content,
        structured_data=operation.structured_data,
        provenance=_output_provenance(context, operation),
        confidence=operation.confidence,
        importance=operation.importance,
        utility_score=sum(memory.utility_score for memory in source_memories) / len(source_memories),
        observed_at=max(memory.observed_at for memory in source_memories),
        valid_from=context.published_at,
        consolidation_generation=context.job.generation,
        created_at=context.published_at,
        updated_at=context.published_at,
    )
    session.add(output)
    return output


def _output_creator(context: MemoryPublicationContext, actor: str | None) -> str | None:
    if context.manifest.namespace_type == "user":
        return context.manifest.namespace_id
    return actor


def _output_provenance(context: MemoryPublicationContext, operation: ConsolidationOperation) -> dict[str, Any]:
    return {
        "consolidation_job_id": context.job.id,
        "input_hash": context.manifest.input_hash,
        "proposal_hash": context.proposal.proposal_hash,
        "source_memory_ids": list(operation.source_memory_ids),
    }


async def supersede_replacements(
    session: Any,
    context: MemoryPublicationContext,
    operation: ConsolidationOperation,
    output_id: str,
    *,
    actor: str | None,
    reason: str | None,
) -> list[dict[str, Any]]:
    results = []
    for memory_id in operation.replace_memory_ids:
        replacement = context.source_by_id[memory_id]
        expected = replacement.state_version
        await _supersede_memory(session, context, memory_id, expected)
        _audit_superseded(session, context, memory_id, output_id, expected, actor=actor, reason=reason)
        results.append(
            {
                "memory_id": memory_id,
                "state_version_before": expected,
                "state_version_after": expected + 1,
                "replacement_id": output_id,
            }
        )
    return results


async def _supersede_memory(
    session: Any,
    context: MemoryPublicationContext,
    memory_id: str,
    expected_state_version: int,
) -> None:
    result = await session.execute(
        update(PersistedMemoryRecord)
        .where(
            PersistedMemoryRecord.id == memory_id,
            PersistedMemoryRecord.status == MemoryStatus.active.value,
            PersistedMemoryRecord.state_version == expected_state_version,
        )
        .values(
            status=MemoryStatus.superseded.value,
            state_version=expected_state_version + 1,
            valid_to=context.published_at,
            updated_at=context.published_at,
        )
    )
    if result.rowcount != 1:
        raise ConsolidationConflictError(f"Memory changed during publication: {memory_id}")


def _audit_superseded(
    session: Any,
    context: MemoryPublicationContext,
    memory_id: str,
    output_id: str,
    expected_state_version: int,
    *,
    actor: str | None,
    reason: str | None,
) -> None:
    record_memory_audit(
        session,
        memory_id,
        "consolidation_superseded",
        actor,
        reason,
        {
            "job_id": context.job.id,
            "generation": context.job.generation,
            "replacement_id": output_id,
            "state_version_before": expected_state_version,
        },
        context.published_at,
    )
