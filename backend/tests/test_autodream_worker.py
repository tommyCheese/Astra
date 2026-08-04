import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.model_base import Base
from app.memory import autodream as autodream_module
from app.memory.autodream import AutoDreamService
from app.memory.domain import MemoryNamespace, MemoryNamespaceType
from app.repositories.memories import MemoryRepository


class ForbiddenSessionFactory:
    def __call__(self):
        raise AssertionError("disabled AutoDream must not open a database session")


def test_autodream_settings_reject_inconsistent_bounds():
    with pytest.raises(ValidationError, match="minimum candidates"):
        Settings(
            agent_memory_autodream_min_candidates=10,
            agent_memory_autodream_max_records_per_job=5,
        )


async def test_disabled_service_has_no_database_side_effects():
    service = AutoDreamService(
        Settings(agent_memory_autodream_enabled=False),
        ForbiddenSessionFactory(),
    )

    await service.startup()
    assert await service.run_once() == {
        "enabled": False,
        "created_job_ids": [],
        "processed_job_ids": [],
    }
    await service.shutdown()


async def test_enabled_worker_scans_and_processes_bounded_namespace():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        repository = MemoryRepository(session)
        for index, key in enumerate(("Project DB", "project-db", "project_db")):
            await repository.create(
                namespace=MemoryNamespace(
                    MemoryNamespaceType.session,
                    "session-1",
                ),
                scope="session",
                kind="semantic_fact",
                memory_key=key,
                content="Astra uses PostgreSQL.",
                provenance={
                    "url": f"https://example.test/source/{index}",
                },
                confidence=0.9,
            )

    service = AutoDreamService(
        Settings(
            agent_memory_autodream_enabled=True,
            agent_memory_autodream_min_candidates=2,
            agent_memory_autodream_max_records_per_job=2,
            agent_memory_autodream_batch_size=1,
            agent_memory_autodream_lease_seconds=30,
            agent_memory_autodream_cooldown_seconds=0,
        ),
        sessions,
    )
    result = await service.run_once()

    assert result["enabled"] is True
    assert len(result["created_job_ids"]) == 1
    assert result["processed_job_ids"] == result["created_job_ids"]
    async with sessions() as session:
        from app.repositories.memory_consolidation import (
            MemoryConsolidationRepository,
        )

        jobs = await MemoryConsolidationRepository(session).list_jobs()
        assert jobs[0].status == "proposed"
        assert len(jobs[0].input_manifest["items"]) == 2
    await engine.dispose()


async def test_worker_isolates_one_failed_job_and_continues(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        repository = MemoryRepository(session)
        for namespace_id in ("session-a", "session-b"):
            for index, key in enumerate(("Project DB", "project-db")):
                await repository.create(
                    namespace=MemoryNamespace(
                        MemoryNamespaceType.session,
                        namespace_id,
                    ),
                    scope="session",
                    kind="semantic_fact",
                    memory_key=key,
                    content="Astra uses PostgreSQL.",
                    provenance={
                        "url": (f"https://example.test/{namespace_id}/{index}"),
                    },
                    confidence=0.9,
                )

    original = autodream_module.deterministic_duplicate_proposal
    call_count = 0

    def fail_first(manifest):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("isolated test failure")
        return original(manifest)

    monkeypatch.setattr(
        autodream_module,
        "deterministic_duplicate_proposal",
        fail_first,
    )
    service = AutoDreamService(
        Settings(
            agent_memory_autodream_enabled=True,
            agent_memory_autodream_min_candidates=2,
            agent_memory_autodream_max_records_per_job=2,
            agent_memory_autodream_batch_size=2,
            agent_memory_autodream_lease_seconds=30,
            agent_memory_autodream_cooldown_seconds=0,
        ),
        sessions,
    )
    result = await service.run_once()

    assert len(result["created_job_ids"]) == 2
    assert len(result["processed_job_ids"]) == 2
    async with sessions() as session:
        from app.repositories.memory_consolidation import (
            MemoryConsolidationRepository,
        )

        jobs = await MemoryConsolidationRepository(session).list_jobs()
        assert {job.status for job in jobs} == {"failed", "proposed"}
        failed = next(job for job in jobs if job.status == "failed")
        assert failed.error["code"] == "RuntimeError"
    await engine.dispose()
