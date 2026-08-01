import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.memory_consolidation import router
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_session
from app.memory.domain import MemoryNamespace, MemoryNamespaceType
from app.repositories.memories import MemoryRepository


@pytest.fixture
async def consolidation_client():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        model_provider="mock",
        agent_memory_autodream_lease_seconds=30,
    )

    async def override_session():
        async with sessions() as session:
            yield session

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    async with sessions() as session:
        repository = MemoryRepository(session)
        for index, key in enumerate(("Project DB", "project-db")):
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

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client
    await engine.dispose()


async def test_trigger_list_detail_publish_and_rollback(consolidation_client):
    triggered = await consolidation_client.post(
        "/api/memory/consolidation/jobs",
        json={
            "namespace_type": "session",
            "namespace_id": "session-1",
            "idempotency_key": "api:test",
        },
    )
    assert triggered.status_code == 200
    proposed = triggered.json()
    assert proposed["status"] == "proposed"
    assert proposed["validation"]["valid"] is True
    assert "content" not in str(proposed["profile_snapshot"])

    duplicate = await consolidation_client.post(
        "/api/memory/consolidation/jobs",
        json={
            "namespace_type": "session",
            "namespace_id": "session-1",
            "idempotency_key": "api:test",
        },
    )
    assert duplicate.json()["id"] == proposed["id"]

    listed = await consolidation_client.get(
        "/api/memory/consolidation/jobs",
        params={
            "namespace_type": "session",
            "namespace_id": "session-1",
        },
    )
    assert [job["id"] for job in listed.json()["jobs"]] == [proposed["id"]]

    detail = await consolidation_client.get(f"/api/memory/consolidation/jobs/{proposed['id']}")
    assert detail.status_code == 200
    assert detail.json()["input_hash"] == proposed["input_hash"]

    stale = await consolidation_client.post(
        f"/api/memory/consolidation/jobs/{proposed['id']}/publish",
        json={"expected_state_version": proposed["state_version"] + 1},
    )
    assert stale.status_code == 409

    published = await consolidation_client.post(
        f"/api/memory/consolidation/jobs/{proposed['id']}/publish",
        json={
            "expected_state_version": proposed["state_version"],
            "actor": "api-test",
            "reason": "reviewed",
        },
    )
    assert published.status_code == 200
    published_job = published.json()
    assert published_job["status"] == "published"
    assert len(published_job["publish_result"]["outputs"]) == 1

    rolled_back = await consolidation_client.post(
        f"/api/memory/consolidation/jobs/{proposed['id']}/rollback",
        json={
            "expected_state_version": published_job["state_version"],
            "actor": "api-test",
            "reason": "revert",
        },
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["rollback_of_id"] == proposed["id"]


async def test_api_rejects_cross_namespace_idempotency_reuse(
    consolidation_client,
):
    first = await consolidation_client.post(
        "/api/memory/consolidation/jobs",
        json={
            "namespace_type": "session",
            "namespace_id": "session-1",
            "idempotency_key": "api:shared",
        },
    )
    assert first.status_code == 200

    conflict = await consolidation_client.post(
        "/api/memory/consolidation/jobs",
        json={
            "namespace_type": "session",
            "namespace_id": "session-2",
            "idempotency_key": "api:shared",
        },
    )
    assert conflict.status_code == 409


async def test_api_rejects_new_workspace_consolidation(consolidation_client):
    response = await consolidation_client.post(
        "/api/memory/consolidation/jobs",
        json={
            "namespace_type": "workspace",
            "namespace_id": "legacy-workspace",
            "idempotency_key": "api:legacy-workspace",
        },
    )
    assert response.status_code == 422
