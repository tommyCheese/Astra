from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select

from app.db.models.memory import MemoryLinkRecord, MemoryRecord, MemorySourceRecord
from app.memory.consolidation.models import ConsolidationInputManifest, ConsolidationOperation


async def next_memory_version(
    session: Any, manifest: ConsolidationInputManifest, memory_key: str
) -> int:
    current = await session.scalar(
        select(func.coalesce(func.max(MemoryRecord.version), 0)).where(
            MemoryRecord.namespace_type == manifest.namespace_type,
            MemoryRecord.namespace_id == manifest.namespace_id,
            MemoryRecord.memory_key == memory_key,
        )
    )
    return int(current) + 1


def copy_sources_and_create_links(
    session: Any,
    operation: ConsolidationOperation,
    source_memories: list[MemoryRecord],
    output_id: str,
    *,
    job_id: str,
    published_at: datetime,
) -> None:
    copied_sources: set[tuple[str, str]] = set()
    for source_memory in source_memories:
        for source in source_memory.sources:
            identity = (source.source_kind, source.source_ref)
            if _source_can_be_copied(source, identity, copied_sources):
                copied_sources.add(identity)
                session.add(_copy_source(source, output_id, published_at))
        session.add(
            MemoryLinkRecord(
                source_memory_id=output_id,
                target_memory_id=source_memory.id,
                relation=_source_relation(source_memory.id, operation),
                link_data={
                    "consolidation_job_id": job_id,
                    "operation_id": operation.operation_id,
                },
                created_at=published_at,
            )
        )


def _source_can_be_copied(
    source: MemorySourceRecord,
    identity: tuple[str, str],
    copied_sources: set[tuple[str, str]],
) -> bool:
    return identity not in copied_sources and source.accessible and source.revoked_at is None


def _copy_source(
    source: MemorySourceRecord, output_id: str, created_at: datetime
) -> MemorySourceRecord:
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


def _source_relation(source_memory_id: str, operation: ConsolidationOperation) -> str:
    if source_memory_id in operation.replace_memory_ids:
        return "supersedes"
    return "derived_from"
