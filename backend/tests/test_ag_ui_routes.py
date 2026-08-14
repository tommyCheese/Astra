import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.common.core.config import AstraRuntimeSettings, get_settings
from app.infrastructure.db.models.ag_ui import AgUiRunBindingRecord
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.metadata import metadata
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.session import get_session
from app.infrastructure.repositories.ag_ui_bindings import AgUiBindingRepository, RunBindingCreate
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.interfaces.ag_ui import routes as ag_ui_routes
from app.main import create_app


@pytest.fixture
async def ag_ui_client(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def session_dependency():
        async with sessions() as session:
            yield session

    settings = AstraRuntimeSettings(
        ag_ui_enabled=True,
        ag_ui_max_request_bytes=1_024,
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
        runtime_profile_path=str(tmp_path / "runtime-profile.json"),
    )
    app = create_app(settings, session_factory=sessions)

    async def complete_run(run_id, runtime_settings):
        async with sessions() as session:
            repository = RunUnitOfWork(session)
            await repository.add_event(run_id, "answer.delta", {"delta": "Hello"})
            await repository.add_event(run_id, "answer.completed", {"content": "Hello"})
            run = await session.get(RunRecord, run_id)
            run.status = "completed"
            await repository.add_event(run_id, "run.status_changed", {"status": "completed"})
            await session.commit()

    monkeypatch.setattr(app.state.container.run_dispatcher, "_run_starter", complete_run)
    monkeypatch.setattr(ag_ui_routes, "SessionLocal", sessions)
    app.dependency_overrides[get_session] = session_dependency
    app.dependency_overrides[get_settings] = lambda: settings
    async with sessions() as session:
        local = TaskRecord(title="Local", description="Local", created_by="local-user")
        other = TaskRecord(title="Other", description="Other", created_by="other-user")
        session.add_all([local, other])
        await session.commit()
        local_id, other_id = local.id, other.id
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client.sessions = sessions
        client.local_thread = local_id
        client.other_thread = other_id
        yield client
    await engine.dispose()


def payload(thread_id: str, *, run_id: str = "protocol-run-1") -> dict:
    return {
        "threadId": thread_id,
        "runId": run_id,
        "state": {},
        "messages": [{"id": "user-1", "role": "user", "content": "Hello"}],
        "tools": [],
        "context": [],
        "forwardedProps": {"astra": {"profileVersion": "astra-ag-ui-v1"}},
    }


async def test_valid_request_streams_and_duplicate_protocol_run_is_idempotent(ag_ui_client) -> None:
    request = payload(ag_ui_client.local_thread)
    first = await ag_ui_client.post("/api/ag-ui", json=request)
    second = await ag_ui_client.post("/api/ag-ui", json=request)
    assert first.status_code == second.status_code == 200
    events = [json.loads(frame[6:]) for frame in first.text.strip().split("\n\n")]
    assert events[0]["type"] == "RUN_STARTED"
    assert events[-1]["type"] == "RUN_FINISHED"
    async with ag_ui_client.sessions() as session:
        run_count = await session.scalar(select(func.count()).select_from(RunRecord))
        binding_count = await session.scalar(select(func.count()).select_from(AgUiRunBindingRecord))
    assert run_count == 1 and binding_count == 1


async def test_api_rejects_malformed_forged_unknown_oversized_and_unauthorized_input(ag_ui_client) -> None:
    base = payload(ag_ui_client.local_thread)
    malformed = await ag_ui_client.post("/api/ag-ui", json={**base, "messages": []})
    forged = await ag_ui_client.post(
        "/api/ag-ui",
        json={**base, "runId": "forged", "tools": [{"name": "shell", "description": "", "parameters": {}}]},
    )
    unknown = await ag_ui_client.post("/api/ag-ui", json={**base, "runId": "unknown", "unknown": True})
    oversized = await ag_ui_client.post(
        "/api/ag-ui",
        json={**base, "runId": "large", "messages": [{"id": "u", "role": "user", "content": "x" * 2_000}]},
    )
    unauthorized = await ag_ui_client.post("/api/ag-ui", json=payload(ag_ui_client.other_thread, run_id="other"))
    assert malformed.status_code == forged.status_code == unknown.status_code == oversized.status_code == 422
    assert unauthorized.status_code == 404
    assert unauthorized.json()["error"]["code"] == "AG_UI_THREAD_NOT_FOUND"


async def test_explicit_cancellation_is_authorized_and_concurrently_idempotent(ag_ui_client) -> None:
    async with ag_ui_client.sessions() as session:
        run = RunRecord(task_id=ag_ui_client.local_thread)
        session.add(run)
        await session.flush()
        await AgUiBindingRepository(session).create_run_binding(
            RunBindingCreate(
                principal_id="local-user",
                thread_id=ag_ui_client.local_thread,
                protocol_run_id="cancel-run",
                internal_task_id=ag_ui_client.local_thread,
                internal_run_id=run.id,
                profile_version="astra-ag-ui-v1",
                input_fingerprint="c" * 64,
            )
        )
        await session.commit()

    endpoint = f"/api/ag-ui/runs/cancel-run/cancel?threadId={ag_ui_client.local_thread}"
    first, second = await asyncio.gather(ag_ui_client.post(endpoint), ag_ui_client.post(endpoint))
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "cancelled"
    hidden = await ag_ui_client.post(f"/api/ag-ui/runs/cancel-run/cancel?threadId={ag_ui_client.other_thread}")
    assert hidden.status_code == 404
