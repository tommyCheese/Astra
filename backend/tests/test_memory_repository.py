from datetime import timedelta

import pytest

from app.db.models import TaskRecord, utc_now
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
    workspace_id: str | None = None,
    created_by: str | None = None,
):
    run = await RunRepository(session).create_task_run(
        "Memory repository test",
        {"provider": "mock", "model": "mock"},
    )
    task = await session.get(TaskRecord, run.task_id)
    task.workspace_id = workspace_id
    task.created_by = created_by
    await session.commit()
    return run


async def test_namespace_derivation_never_shares_missing_identities(session):
    repository = MemoryRepository(session)
    isolated_run = await _run_with_identity(session)
    identified_run = await _run_with_identity(
        session,
        workspace_id="workspace-a",
        created_by="user-a",
    )

    isolated = await repository.namespaces_for_run(isolated_run.id)
    identified = await repository.namespaces_for_run(identified_run.id)

    assert [(item.type.value, item.id) for item in isolated] == [
        ("run", isolated_run.id),
        ("task", isolated_run.task_id),
    ]
    assert ("workspace", "workspace-a") in [
        (item.type.value, item.id) for item in identified
    ]
    assert ("user", "user-a") in [(item.type.value, item.id) for item in identified]

    with pytest.raises(MemoryValidationError, match="workspace identity"):
        await repository.create(
            run_id=isolated_run.id,
            scope="workspace",
            kind="semantic_fact",
            content="Never shared",
            provenance={"run_id": isolated_run.id},
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
    assert [(item.source_kind, item.source_ref) for item in active.sources] == [
        ("run", run.id)
    ]

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
    run = await _run_with_identity(session, workspace_id="workspace-a")
    repository = MemoryRepository(session)
    original = await repository.create(
        run_id=run.id,
        scope="workspace",
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
        namespace=MemoryNamespace(MemoryNamespaceType.workspace, "workspace-a"),
        memory_key="project.runtime",
    )
    assert [(item.version, item.status) for item in history] == [
        (1, "superseded"),
        (2, "active"),
    ]


async def test_list_filters_expired_inactive_and_other_namespaces(session):
    run_a = await _run_with_identity(session, workspace_id="workspace-a")
    run_b = await _run_with_identity(session, workspace_id="workspace-b")
    repository = MemoryRepository(session)
    active = await repository.create(
        run_id=run_a.id,
        scope="workspace",
        kind="semantic_fact",
        content="Workspace A fact",
        provenance={"run_id": run_a.id},
        confidence=0.9,
    )
    await repository.create(
        run_id=run_a.id,
        scope="workspace",
        kind="semantic_fact",
        content="Expired fact",
        provenance={"run_id": run_a.id},
        confidence=0.9,
        expires_at=utc_now() - timedelta(seconds=1),
    )
    await repository.create(
        run_id=run_b.id,
        scope="workspace",
        kind="semantic_fact",
        content="Workspace B fact",
        provenance={"run_id": run_b.id},
        confidence=0.9,
    )

    records = await repository.list_records(
        namespaces=[
            MemoryNamespace(MemoryNamespaceType.workspace, "workspace-a")
        ],
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
    run_a = await _run_with_identity(session, workspace_id="workspace-a")
    run_b = await _run_with_identity(session, workspace_id="workspace-b")
    repository = MemoryRepository(session)
    memory_a = await repository.create(
        run_id=run_a.id,
        scope="workspace",
        kind="semantic_fact",
        content="A",
        provenance={"run_id": run_a.id},
        confidence=0.8,
    )
    memory_b = await repository.create(
        run_id=run_b.id,
        scope="workspace",
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
