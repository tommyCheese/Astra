from __future__ import annotations

from typing import Any

from sqlalchemy import update

from app.db.model_base import uuid_str
from app.db.models.memory import MemoryRecord
from app.memory.consolidation.models import ConsolidationConflictError, ConsolidationOperation
from app.memory.domain import MemoryStatus
from app.repositories.memory_audit import record_memory_audit
from app.repositories.memory_consolidation_sources import next_memory_version
from app.repositories.memory_consolidation_types import PublicationContext


async def create_output_memory(
    session: Any,
    context: PublicationContext,
    operation: ConsolidationOperation,
    source_memories: list[MemoryRecord],
    actor: str | None,
) -> MemoryRecord:
    version = await next_memory_version(session, context.manifest, operation.memory_key)
    run_ids = {memory.run_id for memory in source_memories if memory.run_id}
    output = MemoryRecord(
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
        utility_score=sum(memory.utility_score for memory in source_memories)
        / len(source_memories),
        observed_at=max(memory.observed_at for memory in source_memories),
        valid_from=context.published_at,
        consolidation_generation=context.job.generation,
        created_at=context.published_at,
        updated_at=context.published_at,
    )
    session.add(output)
    return output


def _output_creator(context: PublicationContext, actor: str | None) -> str | None:
    if context.manifest.namespace_type == "user":
        return context.manifest.namespace_id
    return actor


def _output_provenance(
    context: PublicationContext, operation: ConsolidationOperation
) -> dict[str, Any]:
    return {
        "consolidation_job_id": context.job.id,
        "input_hash": context.manifest.input_hash,
        "proposal_hash": context.proposal.proposal_hash,
        "source_memory_ids": list(operation.source_memory_ids),
    }


async def supersede_replacements(
    session: Any,
    context: PublicationContext,
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
        _audit_superseded(
            session, context, memory_id, output_id, expected, actor=actor, reason=reason
        )
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
    context: PublicationContext,
    memory_id: str,
    expected_state_version: int,
) -> None:
    result = await session.execute(
        update(MemoryRecord)
        .where(
            MemoryRecord.id == memory_id,
            MemoryRecord.status == MemoryStatus.active.value,
            MemoryRecord.state_version == expected_state_version,
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
    context: PublicationContext,
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
