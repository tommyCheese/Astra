import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import runs as runs_api
from app.api import runtime as runtime_api
from app.core.config import Settings, get_settings
from app.db.models import Base
from app.db.session import get_session
from app.main import create_app


@pytest.fixture
async def app_client(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with Session() as session:
            yield session

    async def noop_runner(run_id, settings):
        return None

    monkeypatch.setattr(runs_api, "start_run_in_process", noop_runner)
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    settings = Settings(model_provider="mock", artifact_store_path=str(tmp_path / "artifacts"))
    app.dependency_overrides[get_settings] = lambda: settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client._astra_session = Session
        client._astra_settings = settings
        yield client
    await engine.dispose()


async def test_create_run_rejects_empty_goal(app_client):
    response = await app_client.post("/api/runs", json={"goal": " "})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "GOAL_REQUIRED"
    assert error["type"] == "validation.input_invalid"
    assert error["trace_id"].startswith("req_")


async def test_runtime_build_uses_stable_validation_error_contract(app_client):
    response = await app_client.post(
        "/api/runtime/build",
        json={"dependencies": [{"name": "unsafe package", "version": "latest"}]},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "RUNTIME_DEPENDENCY_INVALID"
    assert error["type"] == "validation.input_invalid"
    assert error["trace_id"].startswith("req_")


async def test_runtime_build_defaults_missing_version_to_latest(app_client, monkeypatch):
    captured = []

    async def start(dependencies):
        captured.extend(dependencies)
        return {"dependencies": dependencies, "build": {"status": "queued"}}

    monkeypatch.setattr(runtime_api.service, "start", start)
    response = await app_client.post("/api/runtime/build", json={"dependencies": [{"name": "polars"}]})

    assert response.status_code == 200
    assert captured == [{"name": "polars", "version": ""}]


async def test_artifact_content_enforces_workspace_scope_without_leaking_storage_key(app_client, tmp_path):
    from app.artifacts import LocalArtifactStore
    from app.repositories.runs import RunRepository

    source = tmp_path / "chart.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nmock")
    store = LocalArtifactStore(app_client._astra_settings.artifact_store_path)
    key = store.put(source, ".png")
    async with app_client._astra_session() as session:
        repo = RunRepository(session)
        run = await repo.create_task_run("workspace chart", {"provider": "mock"})
        run.task.workspace_id = "workspace-a"
        artifact = await repo.create_artifact(run.id, "chart_image", storage_key=key, mime_type="image/png", size_bytes=12, checksum="checksum", security_status="verified")
        artifact_id = artifact.id
        await session.commit()

    denied = await app_client.get(f"/api/artifacts/{artifact_id}/content", headers={"X-Astra-Workspace-Id": "workspace-b"})
    allowed = await app_client.get(f"/api/artifacts/{artifact_id}/content", headers={"X-Astra-Workspace-Id": "workspace-a"})
    assert denied.status_code == 404
    assert key not in denied.text
    assert allowed.status_code == 200


async def test_create_and_get_run(app_client):
    created = await app_client.post("/api/runs", json={"goal": "查询 Astra"})
    assert created.status_code == 200
    run_id = created.json()["run_id"]

    loaded = await app_client.get(f"/api/runs/{run_id}")
    assert loaded.status_code == 200
    body = loaded.json()
    assert body["id"] == run_id
    assert body["mode"] == "web_agent"
    assert "turns" in body
    assert "memories" in body
    assert "chat_messages" in body


async def test_create_run_compiles_reasoning_policy(app_client):
    created = await app_client.post("/api/runs", json={"goal": "分析复杂问题", "reasoning_policy": {"reasoning_effort": "deep", "planning_strategy": "plan_first", "reflection_enabled": False, "reflection_trigger": "adaptive", "execution_mode": "request_approval", "verification_level": "strict"}})
    run_id = created.json()["run_id"]
    body = (await app_client.get(f"/api/runs/{run_id}")).json()
    assert body["reasoning_policy"]["requested"]["reasoning_effort"] == "deep"
    assert body["reasoning_policy"]["effective"]["budgets"]["max_reflections"] == 6


async def test_resume_requires_waiting_run(app_client):
    created = await app_client.post("/api/runs", json={"goal": "普通任务"})
    response = await app_client.post(f"/api/runs/{created.json()['run_id']}/resume", json={"content": "继续"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_NOT_WAITING"


async def test_missing_run_uses_safe_error_envelope(app_client):
    response = await app_client.get("/api/runs/missing")
    assert response.status_code == 404
    assert response.json()["error"] == {
        "type": "resource.not_found",
        "code": "RUN_NOT_FOUND",
        "message": "找不到指定运行记录。",
        "retryable": False,
        "trace_id": response.json()["error"]["trace_id"],
        "details": {},
    }
