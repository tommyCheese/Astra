from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.conversation_lifecycle import ConversationLifecycleService
from app.conversation_retention import ConversationRetentionService
from app.core.config import Settings
from app.db.model_base import Base
from app.db.models.conversations import ConversationShareRecord, TaskRecord
from app.db.models.evolution import (
    AgentEvolutionAuditRecord,
    AgentEvolutionCandidateRecord,
    AgentEvolutionSourceRecord,
)
from app.db.models.memory import MemoryAuditRecord, MemorySourceRecord
from app.repositories.conversations import ConversationRepository
from app.repositories.memories import MemoryRepository
from app.repositories.run_unit_of_work import RunUnitOfWork


@pytest.fixture
async def retention_database():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def create_conversation(
    factory,
    *,
    goal: str,
    updated_at: datetime,
    status: str = "completed",
    pinned: bool = False,
    shared: bool = False,
) -> str:
    async with factory() as session:
        run = await RunUnitOfWork(session).create_task_run(goal, {"provider": "mock"})
        await RunUnitOfWork(session).update_run_status(run.id, status, summary=goal)
        task = await session.get(TaskRecord, run.task_id)
        assert task is not None
        task.updated_at = updated_at
        task.pinned_at = updated_at if pinned else None
        if shared:
            session.add(
                ConversationShareRecord(
                    conversation_id=task.id,
                    token=f"token-{task.id}",
                    snapshot={},
                    active=True,
                )
            )
        await session.commit()
        return task.id


def retention_settings(tmp_path, **overrides) -> Settings:
    values = {
        "model_provider": "mock",
        "conversation_retention_enabled": True,
        "conversation_retention_days": 30,
        "conversation_retention_sweep_seconds": 60,
        "conversation_retention_batch_size": 100,
        "artifact_store_path": str(tmp_path / "artifacts"),
        "task_workspace_store_path": str(tmp_path / "workspaces"),
    }
    values.update(overrides)
    return Settings(**values)


def test_retention_settings_reject_unsafe_bounds():
    with pytest.raises(ValidationError):
        Settings(conversation_retention_days=0)
    with pytest.raises(ValidationError):
        Settings(conversation_retention_sweep_seconds=59)
    with pytest.raises(ValidationError):
        Settings(conversation_retention_batch_size=0)


async def test_candidate_selection_protects_recent_pinned_shared_active_and_empty(
    retention_database,
):
    now = datetime.now(timezone.utc)
    oldest = await create_conversation(
        retention_database, goal="oldest", updated_at=now - timedelta(days=90)
    )
    newer = await create_conversation(
        retention_database, goal="newer", updated_at=now - timedelta(days=60)
    )
    await create_conversation(retention_database, goal="recent", updated_at=now - timedelta(days=5))
    await create_conversation(
        retention_database,
        goal="pinned",
        updated_at=now - timedelta(days=90),
        pinned=True,
    )
    await create_conversation(
        retention_database,
        goal="shared",
        updated_at=now - timedelta(days=90),
        shared=True,
    )
    await create_conversation(
        retention_database,
        goal="active",
        updated_at=now - timedelta(days=90),
        status="running",
    )
    async with retention_database() as session:
        session.add(
            TaskRecord(
                title="empty",
                description="empty",
                status="created",
                updated_at=now - timedelta(days=90),
            )
        )
        await session.commit()
        repo = ConversationRepository(session)
        cutoff = now - timedelta(days=30)
        assert await repo.retention_candidate_ids(cutoff=cutoff, limit=1) == [oldest]
        assert await repo.retention_candidate_ids(cutoff=cutoff, limit=10) == [
            oldest,
            newer,
        ]


async def test_terminal_run_transition_refreshes_conversation_activity(
    retention_database,
):
    old = datetime.now(timezone.utc) - timedelta(days=90)
    async with retention_database() as session:
        repo = RunUnitOfWork(session)
        run = await repo.create_task_run("long-running", {"provider": "mock"})
        task = await session.get(TaskRecord, run.task_id)
        assert task is not None
        task.updated_at = old
        await session.commit()

        await repo.update_run_status(run.id, "completed", summary="done")

        assert task.updated_at > old + timedelta(days=1)


async def test_lifecycle_deletes_database_artifact_and_workspace(retention_database, tmp_path):
    now = datetime.now(timezone.utc)
    conversation_id = await create_conversation(
        retention_database, goal="cleanup", updated_at=now - timedelta(days=90)
    )
    settings = retention_settings(tmp_path)
    artifact_key = "2026/07/28/result.txt"
    artifact_path = tmp_path / "artifacts" / artifact_key
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("result")
    workspace_path = tmp_path / "workspaces" / "tasks" / conversation_id
    workspace_path.mkdir(parents=True)
    (workspace_path / "notes.txt").write_text("notes")

    async with retention_database() as session:
        task = await ConversationRepository(session).get(conversation_id)
        assert task is not None
        run = task.runs[0]
        await RunUnitOfWork(session).create_artifact(
            run.id,
            "test",
            storage_key=artifact_key,
            security_status="verified",
        )
        outcome = await ConversationLifecycleService(settings).delete(
            ConversationRepository(session), task
        )
        assert outcome.artifact_keys == 1
        assert outcome.cleanup_failures == 0
        assert await session.get(TaskRecord, conversation_id) is None

    assert not artifact_path.exists()
    assert not workspace_path.exists()


async def test_lifecycle_revalidates_memory_and_evolution_sources_before_deletion(
    retention_database,
    tmp_path,
):
    settings = retention_settings(tmp_path)
    async with retention_database() as session:
        runs = RunUnitOfWork(session)
        deleted_run = await runs.create_task_run(
            "待删除来源",
            {"provider": "mock"},
            session_id="session-shared",
        )
        retained_run = await runs.create_task_run(
            "独立来源",
            {"provider": "mock"},
            session_id="session-shared",
        )
        deleted_task = await session.get(TaskRecord, deleted_run.task_id)
        retained_task = await session.get(TaskRecord, retained_run.task_id)
        assert deleted_task is not None and retained_task is not None
        deleted_task.workspace_id = "workspace-shared"
        retained_task.workspace_id = "workspace-shared"
        await runs.update_run_status(deleted_run.id, "completed", summary="done")
        await runs.update_run_status(retained_run.id, "completed", summary="done")
        await session.commit()

        memories = MemoryRepository(session)
        supported = await memories.create(
            run_id=deleted_run.id,
            scope="session",
            kind="semantic_fact",
            memory_key="session:supported",
            content="仍有独立证据支持。",
            provenance={"run_id": deleted_run.id},
            confidence=0.9,
        )
        session.add(
            MemorySourceRecord(
                memory_id=supported.id,
                source_kind="run",
                source_ref=retained_run.id,
                source_hash="b" * 64,
                run_id=retained_run.id,
                source_data={"run_id": retained_run.id},
                accessible=True,
            )
        )
        unsupported = await memories.create(
            run_id=deleted_run.id,
            scope="session",
            kind="semantic_fact",
            memory_key="session:unsupported",
            content="只有待删除来源支持。",
            provenance={"run_id": deleted_run.id},
            confidence=0.9,
        )
        supported_candidate = AgentEvolutionCandidateRecord(
            candidate_key="procedure:supported",
            revision=1,
            candidate_type="procedure",
            target_component="procedure",
            namespace_type="workspace",
            namespace_id="workspace-shared",
            status="draft",
            state_version=1,
            content={"procedure": "safe"},
            content_digest="c" * 64,
            source_manifest={},
            source_manifest_digest="d" * 64,
            environment_constraints={},
        )
        unsupported_candidate = AgentEvolutionCandidateRecord(
            candidate_key="procedure:unsupported",
            revision=1,
            candidate_type="procedure",
            target_component="procedure",
            namespace_type="workspace",
            namespace_id="workspace-shared",
            status="approved",
            state_version=2,
            content={"procedure": "reviewed"},
            content_digest="e" * 64,
            source_manifest={},
            source_manifest_digest="f" * 64,
            environment_constraints={},
        )
        session.add_all([supported_candidate, unsupported_candidate])
        await session.flush()
        session.add_all(
            [
                AgentEvolutionSourceRecord(
                    candidate_id=supported_candidate.id,
                    source_kind="run",
                    source_ref=deleted_run.id,
                    source_hash="1" * 64,
                    run_id=deleted_run.id,
                    accessible=True,
                ),
                AgentEvolutionSourceRecord(
                    candidate_id=supported_candidate.id,
                    source_kind="run",
                    source_ref=retained_run.id,
                    source_hash="2" * 64,
                    run_id=retained_run.id,
                    accessible=True,
                ),
                AgentEvolutionSourceRecord(
                    candidate_id=unsupported_candidate.id,
                    source_kind="memory",
                    source_ref=unsupported.id,
                    source_hash="3" * 64,
                    run_id=deleted_run.id,
                    memory_id=unsupported.id,
                    accessible=True,
                ),
            ]
        )
        await session.commit()

        task = await ConversationRepository(session).get(deleted_task.id)
        assert task is not None
        await ConversationLifecycleService(settings).delete(
            ConversationRepository(session),
            task,
        )

        await session.refresh(supported)
        await session.refresh(unsupported)
        await session.refresh(supported_candidate)
        await session.refresh(unsupported_candidate)
        assert supported.status == "active"
        assert supported.run_id is None
        assert unsupported.status == "revoked"
        assert unsupported.revoke_reason == "source_conversation_deleted"
        assert supported_candidate.status == "draft"
        assert unsupported_candidate.status == "rejected"
        assert unsupported_candidate.state_version == 3
        assert (
            await session.scalar(
                select(func.count(MemorySourceRecord.id)).where(
                    MemorySourceRecord.memory_id == supported.id
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(MemorySourceRecord.id)).where(
                    MemorySourceRecord.memory_id == unsupported.id
                )
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count(AgentEvolutionSourceRecord.id)).where(
                    AgentEvolutionSourceRecord.candidate_id == supported_candidate.id
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(MemoryAuditRecord.id)).where(
                    MemoryAuditRecord.memory_id == unsupported.id,
                    MemoryAuditRecord.event_type == "revoked_by_source_deletion",
                )
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count(AgentEvolutionAuditRecord.id)).where(
                    AgentEvolutionAuditRecord.candidate_id == unsupported_candidate.id,
                    AgentEvolutionAuditRecord.event_type == "rejected_by_source_deletion",
                )
            )
            == 1
        )


async def test_lifecycle_rejects_escaped_workspace_and_isolates_artifact_failure(
    tmp_path,
):
    class FakeRepo:
        async def delete(self, task):
            return ["bad-key"]

    class BrokenStore:
        def delete(self, key):
            raise OSError("unavailable")

    settings = retention_settings(tmp_path)
    outside_path = tmp_path / "outside"
    outside_path.mkdir()
    (outside_path / "keep.txt").write_text("keep")
    task = TaskRecord(
        id="../../outside",
        title="unsafe",
        description="unsafe",
        status="created",
    )
    outcome = await ConversationLifecycleService(settings, artifact_store=BrokenStore()).delete(
        FakeRepo(), task
    )

    assert outcome.cleanup_failures == 2
    assert (outside_path / "keep.txt").read_text() == "keep"


async def test_retention_sweep_is_bounded_and_deletes_oldest(retention_database, tmp_path):
    now = datetime.now(timezone.utc)
    oldest = await create_conversation(
        retention_database, goal="oldest", updated_at=now - timedelta(days=90)
    )
    remaining = await create_conversation(
        retention_database, goal="remaining", updated_at=now - timedelta(days=60)
    )
    settings = retention_settings(tmp_path, conversation_retention_batch_size=1)
    service = ConversationRetentionService(settings, retention_database)

    result = await service.sweep(now=now)

    assert result.selected == result.deleted == 1
    assert result.skipped == result.failed == 0
    async with retention_database() as session:
        assert await session.get(TaskRecord, oldest) is None
        assert await session.get(TaskRecord, remaining) is not None


async def test_retention_revalidates_candidates_and_counts_race_skip(
    retention_database, tmp_path, monkeypatch
):
    now = datetime.now(timezone.utc)
    conversation_id = await create_conversation(
        retention_database, goal="raced", updated_at=now - timedelta(days=90)
    )

    async def no_longer_eligible(self, candidate_id, *, cutoff):
        return False

    monkeypatch.setattr(ConversationRepository, "is_retention_eligible", no_longer_eligible)
    service = ConversationRetentionService(retention_settings(tmp_path), retention_database)
    result = await service.sweep(now=now)

    assert result.selected == result.skipped == 1
    assert result.deleted == result.failed == 0
    async with retention_database() as session:
        assert await session.get(TaskRecord, conversation_id) is not None


async def test_retention_isolates_deletion_failure(retention_database, tmp_path):
    now = datetime.now(timezone.utc)
    failing_id = await create_conversation(
        retention_database, goal="failing", updated_at=now - timedelta(days=90)
    )
    successful_id = await create_conversation(
        retention_database, goal="successful", updated_at=now - timedelta(days=60)
    )
    settings = retention_settings(tmp_path)
    real_lifecycle = ConversationLifecycleService(settings)

    class PartiallyBrokenLifecycle:
        async def delete(self, repo, task):
            if task.id == failing_id:
                raise RuntimeError("simulated failure")
            return await real_lifecycle.delete(repo, task)

    service = ConversationRetentionService(
        settings,
        retention_database,
        lifecycle=PartiallyBrokenLifecycle(),
    )
    result = await service.sweep(now=now)

    assert result.selected == 2
    assert result.deleted == result.failed == 1
    async with retention_database() as session:
        assert await session.get(TaskRecord, failing_id) is not None
        assert await session.get(TaskRecord, successful_id) is None


async def test_disabled_startup_creates_no_worker_and_enabled_shutdown_is_prompt(
    retention_database, tmp_path
):
    disabled = retention_settings(tmp_path, conversation_retention_enabled=False)
    disabled_service = ConversationRetentionService(disabled, retention_database)
    await disabled_service.startup()
    assert disabled_service._task is None

    enabled_service = ConversationRetentionService(retention_settings(tmp_path), retention_database)
    await enabled_service.startup()
    assert enabled_service._task is not None
    await enabled_service.shutdown()
    assert enabled_service._task is None
