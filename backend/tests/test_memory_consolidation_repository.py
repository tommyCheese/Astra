from datetime import timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.db.models import (
    MemoryAuditRecord,
    MemoryConsolidationJobRecord,
    MemoryRecord,
    MemorySourceRecord,
    utc_now,
)
from app.memory.autodream import AutoDreamProcessor
from app.memory.consolidation import ConsolidationConflictError
from app.memory.domain import MemoryNamespace, MemoryNamespaceType
from app.repositories.memories import MemoryRepository
from app.repositories.memory_consolidation import (
    MemoryConsolidationRepository,
)


async def create_duplicate_memories(
    session,
    *,
    namespace_id: str = "workspace-1",
    count: int = 2,
) -> list[MemoryRecord]:
    repository = MemoryRepository(session)
    records = []
    keys = ["Project DB", "project-db", "project_db", "project.db"]
    for index in range(count):
        records.append(
            await repository.create(
                namespace=MemoryNamespace(
                    MemoryNamespaceType.workspace,
                    namespace_id,
                ),
                scope="workspace",
                kind="semantic_fact",
                memory_key=keys[index],
                content="Astra uses PostgreSQL.",
                provenance={
                    "url": f"https://example.test/source/{index}",
                },
                confidence=0.9 - index * 0.01,
                importance=0.7,
            )
        )
    return records


def autodream_settings(**overrides) -> Settings:
    values = {
        "model_provider": "mock",
        "agent_memory_autodream_min_candidates": 2,
        "agent_memory_autodream_max_records_per_job": 100,
        "agent_memory_autodream_lease_seconds": 30,
    }
    values.update(overrides)
    return Settings(**values)


async def test_prepare_publish_and_audited_rollback_are_atomic(session):
    inputs = await create_duplicate_memories(session)
    input_ids = [memory.id for memory in inputs]
    repository = MemoryConsolidationRepository(session)
    job = await repository.create_job(
        namespace_type="workspace",
        namespace_id="workspace-1",
        idempotency_key="test:publish",
    )

    proposed = await AutoDreamProcessor(autodream_settings()).prepare_job(
        session,
        job.id,
        owner="test-worker",
    )
    assert proposed.status == "proposed"
    assert proposed.validation["valid"] is True
    assert proposed.input_hash == proposed.input_manifest["input_hash"]
    assert proposed.model_usage["calls"] == 0

    published = await repository.publish(
        proposed.id,
        expected_state_version=proposed.state_version,
        actor="tester",
        reason="verified duplicate",
    )
    published_id = published.id
    published_state_version = published.state_version
    assert published.status == "published"
    assert len(published.publish_result["outputs"]) == 1
    output_id = published.publish_result["outputs"][0]["memory_id"]

    output = await session.get(MemoryRecord, output_id)
    assert output is not None
    assert output.status == "active"
    assert output.consolidation_generation == published.generation
    for memory_id in input_ids:
        assert (await session.get(MemoryRecord, memory_id)).status == "superseded"

    rollback = await repository.rollback_published(
        published_id,
        expected_state_version=published_state_version,
        actor="tester",
        reason="operator rollback",
    )
    assert rollback.status == "published"
    assert rollback.rollback_of_id == published_id
    session.expire_all()
    assert (await session.get(MemoryRecord, output_id)).status == "revoked"
    assert {
        (await session.get(MemoryRecord, memory_id)).status
        for memory_id in input_ids
    } == {"active"}
    original = await session.get(MemoryConsolidationJobRecord, published_id)
    assert original.status == "rolled_back"
    audit_types = set(
        (
            await session.scalars(
                select(MemoryAuditRecord.event_type)
            )
        ).all()
    )
    assert {
        "consolidation_published",
        "consolidation_superseded",
        "consolidation_rolled_back",
        "consolidation_restored",
    } <= audit_types


async def test_publication_conflict_changes_no_memory_projection(session):
    inputs = await create_duplicate_memories(session)
    input_ids = [memory.id for memory in inputs]
    repository = MemoryConsolidationRepository(session)
    job = await repository.create_job(
        namespace_type="workspace",
        namespace_id="workspace-1",
        idempotency_key="test:conflict",
    )
    proposed = await AutoDreamProcessor(autodream_settings()).prepare_job(
        session,
        job.id,
        owner="test-worker",
    )
    proposed_id = proposed.id
    proposed_state_version = proposed.state_version
    changed = await session.get(MemoryRecord, input_ids[1])
    changed.content = "A concurrent correction."
    changed.state_version += 1
    await session.commit()

    with pytest.raises(ConsolidationConflictError, match="changed"):
        await repository.publish(
            proposed_id,
            expected_state_version=proposed_state_version,
            actor="tester",
            reason=None,
        )

    session.expire_all()
    conflicted = await session.get(MemoryConsolidationJobRecord, proposed_id)
    assert conflicted.status == "conflict"
    records = (
        await session.scalars(
            select(MemoryRecord).where(
                MemoryRecord.namespace_id == "workspace-1"
            )
        )
    ).all()
    assert len(records) == 2
    assert {record.status for record in records} == {"active"}


async def test_rollback_fails_atomically_when_source_support_is_revoked(session):
    inputs = await create_duplicate_memories(session)
    input_ids = [memory.id for memory in inputs]
    repository = MemoryConsolidationRepository(session)
    job = await repository.create_job(
        namespace_type="workspace",
        namespace_id="workspace-1",
        idempotency_key="test:rollback-source",
    )
    proposed = await AutoDreamProcessor(autodream_settings()).prepare_job(
        session,
        job.id,
        owner="test-worker",
    )
    published = await repository.publish(
        proposed.id,
        expected_state_version=proposed.state_version,
        actor="tester",
        reason=None,
    )
    published_id = published.id
    published_version = published.state_version
    output_id = published.publish_result["outputs"][0]["memory_id"]
    sources = (
        await session.scalars(
            select(MemorySourceRecord).where(
                MemorySourceRecord.memory_id == input_ids[0]
            )
        )
    ).all()
    for source in sources:
        source.accessible = False
        source.revoked_at = utc_now()
    await session.commit()

    with pytest.raises(ConsolidationConflictError, match="lost its supporting"):
        await repository.rollback_published(
            published_id,
            expected_state_version=published_version,
            actor="tester",
            reason="must fail closed",
        )

    session.expire_all()
    assert (await session.get(MemoryRecord, output_id)).status == "active"
    assert {
        (await session.get(MemoryRecord, memory_id)).status
        for memory_id in input_ids
    } == {"superseded"}


async def test_job_idempotency_bounded_input_and_duplicate_prevention(session):
    await create_duplicate_memories(session, count=3)
    repository = MemoryConsolidationRepository(session)
    first = await repository.create_job(
        namespace_type="workspace",
        namespace_id="workspace-1",
        idempotency_key="test:idempotent",
    )
    duplicate = await repository.create_job(
        namespace_type="workspace",
        namespace_id="workspace-1",
        idempotency_key="test:idempotent",
    )
    assert duplicate.id == first.id

    proposed = await AutoDreamProcessor(
        autodream_settings(agent_memory_autodream_max_records_per_job=2)
    ).prepare_job(
        session,
        first.id,
        owner="bounded-worker",
    )
    assert len(proposed.input_manifest["items"]) == 2


async def test_expired_running_lease_is_recovered(session):
    repository = MemoryConsolidationRepository(session)
    job = await repository.create_job(
        namespace_type="task",
        namespace_id="task-1",
        idempotency_key="test:recovery",
    )
    job.status = "running"
    job.lease_owner = "dead-worker"
    job.lease_expires_at = utc_now() - timedelta(seconds=1)
    await session.commit()

    assert await repository.recover_expired() == 1
    recovered = await repository.require(job.id, refresh=True)
    assert recovered.status == "queued"
    assert recovered.lease_owner is None
    assert recovered.error["code"] == "interrupted"


async def test_processor_marks_too_small_working_region_without_side_effects(
    session,
):
    await create_duplicate_memories(session, count=2)
    repository = MemoryConsolidationRepository(session)
    job = await repository.create_job(
        namespace_type="workspace",
        namespace_id="workspace-1",
        idempotency_key="test:minimum",
    )
    result = await AutoDreamProcessor(
        autodream_settings(agent_memory_autodream_min_candidates=3)
    ).prepare_job(
        session,
        job.id,
        owner="test-worker",
    )
    assert result.status == "insufficient_input"
    assert result.input_manifest == {}
    assert {
        memory.status
        for memory in (
            await session.scalars(select(MemoryRecord))
        ).all()
    } == {"active"}
