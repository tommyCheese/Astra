from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.db.models import MemoryAuditRecord, MemorySourceRecord, TaskRecord, utc_now
from app.memory.domain import (
    MemoryConflictError,
    MemoryNamespace,
    MemoryNamespaceType,
    MemoryStatus,
    MemoryValidationError,
)
from app.repositories.memories import MemoryRepository
from app.repositories.runs import RunRepository


async def _run_with_identity(
    session,
    *,
    memory_session_id: str | None = None,
    created_by: str | None = None,
):
    run = await RunRepository(session).create_task_run(
        "Memory repository test",
        {"provider": "mock", "model": "mock"},
        session_id=memory_session_id,
    )
    task = await session.get(TaskRecord, run.task_id)
    task.created_by = created_by
    await session.commit()
    return run


async def test_namespace_derivation_never_shares_missing_identities(session):
    repository = MemoryRepository(session)
    isolated_run = await _run_with_identity(session)
    identified_run = await _run_with_identity(
        session,
        memory_session_id="session-a",
        created_by="user-a",
    )

    isolated = await repository.namespaces_for_run(isolated_run.id)
    identified = await repository.namespaces_for_run(identified_run.id)

    assert [(item.type.value, item.id) for item in isolated] == [
        ("run", isolated_run.id),
        ("task", isolated_run.task_id),
    ]
    assert ("session", "session-a") in [(item.type.value, item.id) for item in identified]
    assert ("user", "user-a") in [(item.type.value, item.id) for item in identified]

    with pytest.raises(MemoryValidationError, match="session identity"):
        await repository.create(
            run_id=isolated_run.id,
            scope="session",
            kind="semantic_fact",
            content="Never shared",
            provenance={"run_id": isolated_run.id},
            confidence=0.8,
        )

    with pytest.raises(MemoryValidationError, match="Unsupported Memory scope"):
        await repository.create(
            run_id=identified_run.id,
            scope="workspace",
            kind="semantic_fact",
            content="Unsupported workspace memory",
            provenance={"run_id": identified_run.id},
            confidence=0.8,
        )


async def test_candidate_transition_uses_sources_and_optimistic_version(session):
    run = await _run_with_identity(session)
    repository = MemoryRepository(session)
    candidate = await repository.create(
        run_id=run.id,
        scope="run",
        kind="semantic_fact",
        content="A candidate fact",
        provenance={"run_id": run.id},
        confidence=0.8,
        status=MemoryStatus.candidate,
    )

    active = await repository.transition(
        candidate.id,
        MemoryStatus.active,
        expected_state_version=1,
        actor="test",
    )
    assert active.status == "active"
    assert active.state_version == 2
    assert [(item.source_kind, item.source_ref) for item in active.sources] == [("run", run.id)]

    with pytest.raises(MemoryConflictError, match="state version"):
        await repository.transition(
            active.id,
            MemoryStatus.quarantined,
            expected_state_version=1,
        )

    revoked = await repository.transition(
        active.id,
        MemoryStatus.revoked,
        expected_state_version=2,
        actor="local_admin",
        reason="incorrect",
    )
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None
    with pytest.raises(ValueError, match="Invalid Memory lifecycle transition"):
        await repository.transition(
            revoked.id,
            MemoryStatus.active,
            expected_state_version=3,
        )


async def test_create_version_supersedes_without_overwriting_history(session):
    run = await _run_with_identity(session, memory_session_id="session-a")
    repository = MemoryRepository(session)
    original = await repository.create(
        run_id=run.id,
        scope="session",
        kind="semantic_fact",
        memory_key="project.runtime",
        content="The project uses Python 3.10",
        provenance={"run_id": run.id},
        confidence=0.9,
    )
    replacement = await repository.create_version(
        original.id,
        expected_state_version=1,
        content="The project uses Python 3.12",
        provenance={"run_id": run.id},
        actor="test",
        reason="runtime upgraded",
    )

    refreshed_original = await repository.require(original.id)
    assert refreshed_original.status == "superseded"
    assert refreshed_original.content == "The project uses Python 3.10"
    assert replacement.status == "active"
    assert replacement.version == 2
    assert replacement.supersedes_id == original.id
    assert replacement.content == "The project uses Python 3.12"

    history = await repository.history(
        namespace=MemoryNamespace(MemoryNamespaceType.session, "session-a"),
        memory_key="project.runtime",
    )
    assert [(item.version, item.status) for item in history] == [
        (1, "superseded"),
        (2, "active"),
    ]


async def test_human_activation_atomically_supersedes_candidate_base(session):
    first_run = await _run_with_identity(session, memory_session_id="session-a")
    second_run = await RunRepository(session).create_task_run(
        "Memory replacement",
        {"provider": "mock", "model": "mock"},
        task_id=first_run.task_id,
        session_id="session-a",
    )
    repository = MemoryRepository(session)
    original = await repository.create(
        run_id=first_run.id,
        scope="session",
        kind="semantic_fact",
        memory_key="project.runtime",
        content="The project uses Python 3.10",
        provenance={"run_id": first_run.id},
        confidence=0.9,
    )
    candidate = await repository.create_candidate_version(
        original.id,
        expected_state_version=1,
        source_run_id=second_run.id,
        content="The project uses Python 3.12",
        provenance={"run_id": second_run.id},
        actor="memory-extractor",
        reason="awaiting review",
    )

    assert candidate.status == "candidate"
    assert (await repository.require(original.id)).status == "active"

    activated = await repository.activate_candidate(
        candidate.id,
        expected_state_version=1,
        actor="local-operator",
        reason="verified upgrade",
    )
    assert activated.status == "active"
    assert activated.state_version == 2
    assert (await repository.require(original.id)).status == "superseded"
    audit_types = list(
        (
            await session.execute(
                select(MemoryAuditRecord.event_type).where(
                    MemoryAuditRecord.memory_id == activated.id
                )
            )
        ).scalars()
    )
    assert audit_types[-1] == "human_activated"

    with pytest.raises(MemoryConflictError, match="state version"):
        await repository.activate_candidate(
            candidate.id,
            expected_state_version=1,
            actor="local-operator",
            reason="duplicate decision",
        )


async def test_human_activation_rejects_candidate_without_accessible_source(session):
    run = await _run_with_identity(session)
    repository = MemoryRepository(session)
    candidate = await repository.create(
        run_id=run.id,
        scope="run",
        kind="semantic_fact",
        content="A pending fact",
        provenance={"run_id": run.id},
        confidence=0.8,
        status=MemoryStatus.candidate,
    )
    await session.execute(
        update(MemorySourceRecord)
        .where(MemorySourceRecord.memory_id == candidate.id)
        .values(accessible=False)
    )
    await session.commit()

    with pytest.raises(MemoryValidationError, match="accessible source"):
        await repository.activate_candidate(
            candidate.id,
            expected_state_version=1,
            actor="local-operator",
            reason="attempt activation",
        )
    assert (await repository.require(candidate.id)).status == "candidate"


async def test_list_filters_expired_inactive_and_other_namespaces(session):
    run_a = await _run_with_identity(session, memory_session_id="session-a")
    run_b = await _run_with_identity(session, memory_session_id="session-b")
    repository = MemoryRepository(session)
    active = await repository.create(
        run_id=run_a.id,
        scope="session",
        kind="semantic_fact",
        content="Workspace A fact",
        provenance={"run_id": run_a.id},
        confidence=0.9,
    )
    await repository.create(
        run_id=run_a.id,
        scope="session",
        kind="semantic_fact",
        content="Expired fact",
        provenance={"run_id": run_a.id},
        confidence=0.9,
        expires_at=utc_now() - timedelta(seconds=1),
    )
    await repository.create(
        run_id=run_b.id,
        scope="session",
        kind="semantic_fact",
        content="Workspace B fact",
        provenance={"run_id": run_b.id},
        confidence=0.9,
    )

    records = await repository.list_records(
        namespaces=[MemoryNamespace(MemoryNamespaceType.session, "session-a")],
        statuses=[MemoryStatus.active],
        include_expired=False,
    )
    assert [item.id for item in records] == [active.id]


async def test_provenance_reference_must_exist_in_source_run(session):
    run = await _run_with_identity(session)
    repository = MemoryRepository(session)

    with pytest.raises(MemoryValidationError, match="artifact_id not found"):
        await repository.create(
            run_id=run.id,
            scope="run",
            kind="semantic_fact",
            content="Unsupported claim",
            provenance={"run_id": run.id, "artifact_id": "missing-artifact"},
            confidence=0.8,
        )


async def test_memory_links_cannot_cross_namespaces(session):
    run_a = await _run_with_identity(session, memory_session_id="session-a")
    run_b = await _run_with_identity(session, memory_session_id="session-b")
    repository = MemoryRepository(session)
    memory_a = await repository.create(
        run_id=run_a.id,
        scope="session",
        kind="semantic_fact",
        content="A",
        provenance={"run_id": run_a.id},
        confidence=0.8,
    )
    memory_b = await repository.create(
        run_id=run_b.id,
        scope="session",
        kind="semantic_fact",
        content="B",
        provenance={"run_id": run_b.id},
        confidence=0.8,
    )

    with pytest.raises(MemoryValidationError, match="cannot cross namespaces"):
        await repository.add_link(
            source_memory_id=memory_a.id,
            target_memory_id=memory_b.id,
            relation="related",
        )


async def test_expiration_is_query_time_safe_before_bounded_materialization(session):
    run = await _run_with_identity(session)
    repository = MemoryRepository(session)
    expired = await repository.create(
        run_id=run.id,
        scope="run",
        kind="semantic_fact",
        content="Expired fact",
        provenance={"run_id": run.id},
        confidence=0.9,
        expires_at=utc_now() - timedelta(seconds=1),
    )

    assert (
        await repository.list_records(
            run_id=run.id,
            statuses=[MemoryStatus.active],
            include_expired=False,
        )
        == []
    )
    assert await repository.materialize_expired(limit=0) == 0
    assert (await repository.require(expired.id)).status == "active"

    assert await repository.materialize_expired(limit=1) == 1
    materialized = await repository.require(expired.id)
    assert materialized.status == "expired"
    assert materialized.state_version == 2
    assert await repository.materialize_expired(limit=1) == 0
