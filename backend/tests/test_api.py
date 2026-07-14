import asyncio
import json

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api import runs as runs_api
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
    settings = Settings(model_provider="mock", artifact_store_path=str(tmp_path / "artifacts"))
    app = create_app(settings)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client._astra_session = Session
        client._astra_settings = settings
        client._astra_runtime_service = app.state.runtime_profile_service
        yield client
    await engine.dispose()


async def test_create_run_rejects_empty_goal(app_client):
    response = await app_client.post("/api/runs", json={"goal": " "})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "GOAL_REQUIRED"
    assert error["type"] == "validation.input_invalid"
    assert error["trace_id"].startswith("req_")


async def test_create_run_rejects_invalid_agent_profile_as_configuration_error(
    app_client, monkeypatch
):
    from app.agent_profile import AgentProfileConfigurationError

    def invalid_profile():
        raise AgentProfileConfigurationError("invalid test profile")

    monkeypatch.setattr(runs_api, "load_agent_profile", invalid_profile)
    response = await app_client.post("/api/runs", json={"goal": "Profile 配置测试"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AGENT_PROFILE_INVALID"
    assert "invalid test profile" not in response.text


async def test_tool_settings_can_be_read_and_updated(app_client):
    loaded = await app_client.get("/api/tools")
    assert loaded.status_code == 200
    assert {tool["name"] for tool in loaded.json()["tools"]} == {
        "web_search", "web_fetch", "chart_render"
    }

    updated = await app_client.put(
        "/api/tools",
        json={"web_search": False, "web_fetch": True, "chart_render": False},
    )
    assert updated.status_code == 200
    states = {tool["name"]: tool["enabled"] for tool in updated.json()["tools"]}
    assert states == {"web_search": False, "web_fetch": True, "chart_render": False}
    reloaded = await app_client.get("/api/tools")
    persisted = {tool["name"]: tool["enabled"] for tool in reloaded.json()["tools"]}
    assert persisted == states


async def test_conversation_strategy_can_be_restored_and_updated(app_client):
    loaded = await app_client.get("/api/preferences/conversation-strategy")
    assert loaded.status_code == 200
    assert loaded.json() == {
        "reasoning_effort": "balanced",
        "max_tool_calls": 8,
        "planning_strategy": "adaptive",
        "reflection_enabled": True,
        "reflection_trigger": "adaptive",
    }

    updated = {
        "reasoning_effort": "deep",
        "max_tool_calls": 32,
        "planning_strategy": "plan_first",
        "reflection_enabled": False,
        "reflection_trigger": "failure_only",
    }
    saved = await app_client.put(
        "/api/preferences/conversation-strategy", json=updated
    )
    assert saved.status_code == 200
    assert saved.json() == updated

    reloaded = await app_client.get("/api/preferences/conversation-strategy")
    assert reloaded.json() == updated


async def test_conversation_strategy_rejects_unknown_values(app_client):
    response = await app_client.put(
        "/api/preferences/conversation-strategy",
        json={
            "reasoning_effort": "unbounded",
            "planning_strategy": "adaptive",
            "reflection_enabled": True,
            "reflection_trigger": "adaptive",
        },
    )
    assert response.status_code == 422


async def test_conversation_strategy_uses_effort_default_when_legacy_client_omits_limit(app_client):
    response = await app_client.put(
        "/api/preferences/conversation-strategy",
        json={
            "reasoning_effort": "deep",
            "planning_strategy": "adaptive",
            "reflection_enabled": True,
            "reflection_trigger": "adaptive",
        },
    )
    assert response.status_code == 200
    assert response.json()["max_tool_calls"] == 16


@pytest.mark.parametrize(
    ("effort", "limit"),
    [("fast", 0), ("fast", 5), ("balanced", 6), ("balanced", 15), ("deep", 16), ("deep", 50)],
)
async def test_conversation_strategy_accepts_tool_limits_for_each_effort(app_client, effort, limit):
    response = await app_client.put(
        "/api/preferences/conversation-strategy",
        json={
            "reasoning_effort": effort,
            "max_tool_calls": limit,
            "planning_strategy": "adaptive",
            "reflection_enabled": True,
            "reflection_trigger": "adaptive",
        },
    )
    assert response.status_code == 200
    assert response.json()["max_tool_calls"] == limit


@pytest.mark.parametrize(
    ("effort", "limit"),
    [("fast", 6), ("balanced", 5), ("balanced", 16), ("deep", 15), ("deep", 51)],
)
async def test_conversation_strategy_rejects_tool_limits_outside_effort_range(app_client, effort, limit):
    response = await app_client.put(
        "/api/preferences/conversation-strategy",
        json={
            "reasoning_effort": effort,
            "max_tool_calls": limit,
            "planning_strategy": "adaptive",
            "reflection_enabled": True,
            "reflection_trigger": "adaptive",
        },
    )
    assert response.status_code == 422


async def test_new_run_uses_persisted_tool_settings(app_client, monkeypatch):
    captured = []

    async def capture_runner(run_id, settings):
        captured.append(settings)

    monkeypatch.setattr(runs_api, "start_run_in_process", capture_runner)
    await app_client.put(
        "/api/tools",
        json={"web_search": False, "web_fetch": True, "chart_render": False},
    )
    created = await app_client.post("/api/runs", json={"goal": "使用持久化工具设置"})
    assert created.status_code == 200
    await asyncio.sleep(0)
    assert captured[0].tool_web_search_enabled is False
    assert captured[0].tool_web_fetch_enabled is True
    assert captured[0].tool_chart_render_enabled is False


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

    monkeypatch.setattr(app_client._astra_runtime_service, "start", start)
    response = await app_client.post(
        "/api/runtime/build", json={"dependencies": [{"name": "polars"}]}
    )

    assert response.status_code == 200
    assert captured == [{"name": "polars", "version": ""}]


async def test_artifact_content_enforces_workspace_scope_without_leaking_storage_key(
    app_client, tmp_path
):
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
        artifact = await repo.create_artifact(
            run.id,
            "chart_image",
            storage_key=key,
            mime_type="image/png",
            size_bytes=12,
            checksum="checksum",
            security_status="verified",
        )
        artifact_id = artifact.id
        await session.commit()

    denied = await app_client.get(
        f"/api/artifacts/{artifact_id}/content", headers={"X-Astra-Workspace-Id": "workspace-b"}
    )
    allowed = await app_client.get(
        f"/api/artifacts/{artifact_id}/content", headers={"X-Astra-Workspace-Id": "workspace-a"}
    )
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
    assert body["agent_profile"]["version"].startswith("profile-")
    assert "content" not in body["agent_profile"]["documents"]["identity"]
    assert "secret" not in str(body["agent_profile"]).lower()


async def test_conversation_management_and_share_lifecycle(app_client):
    from app.db.models import AgentTurnRecord
    from app.repositories.runs import RunRepository

    created = await app_client.post("/api/runs", json={"goal": "需要安全分享的对话"})
    conversation_id = created.json()["task_id"]
    run_id = created.json()["run_id"]
    async with app_client._astra_session() as session:
        await RunRepository(session).update_run_status(run_id, "completed", summary="公开回答")
        session.add(AgentTurnRecord(run_id=run_id, turn_index=1, decision_type="finalize", reasoning_summary="正在整理公开回答", status="completed"))
        await session.commit()

    renamed = await app_client.patch(
        f"/api/conversations/{conversation_id}", json={"title": "用户标题", "pinned": True}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "用户标题"
    assert renamed.json()["title_source"] == "user"
    assert renamed.json()["pinned_at"] is not None

    listed = await app_client.get("/api/conversations")
    assert listed.json()[0]["id"] == conversation_id
    assert listed.json()[0]["title"] == "用户标题"

    shared = await app_client.post(f"/api/conversations/{conversation_id}/share")
    assert shared.status_code == 200
    token = shared.json()["url"].rsplit("/", 1)[-1]
    active_shares = await app_client.get("/api/conversation-shares")
    assert active_shares.status_code == 200
    assert active_shares.json()[0]["conversation_id"] == conversation_id
    assert active_shares.json()[0]["title"] == "用户标题"
    assert active_shares.json()[0]["message_count"] == 2
    public = await app_client.get(f"/api/shared-conversations/{token}")
    assert public.status_code == 200
    assert public.json()["title"] == "用户标题"
    assert public.json()["messages"] == [
        {"role": "user", "content": "需要安全分享的对话", "items": []},
        {"role": "process", "content": "", "items": [{"kind": "reasoning", "title": "思考", "detail": "正在整理公开回答", "status": "completed"}]},
        {"role": "assistant", "content": "正在整理公开回答", "items": []},
    ]
    assert "runs" not in public.json()
    assert "agent_profile" not in public.json()

    removed = await app_client.delete(f"/api/conversations/{conversation_id}")
    assert removed.status_code == 204
    assert (await app_client.get(f"/api/shared-conversations/{token}")).status_code == 404
    assert (await app_client.get(f"/api/conversations/{conversation_id}")).status_code == 404


async def test_run_event_stream_starts_with_ready_signal(app_client, monkeypatch):
    from app.repositories.runs import RunRepository

    created = await app_client.post("/api/runs", json={"goal": "流连接测试"})
    run_id = created.json()["run_id"]
    monkeypatch.setattr(runs_api, "SessionLocal", app_client._astra_session)
    async with app_client._astra_session() as session:
        await RunRepository(session).update_run_status(run_id, "completed", summary="完成")
        await session.commit()

    response = await app_client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert '"type": "stream.ready"' in response.text


async def test_run_event_stream_resumes_after_event_id(app_client, monkeypatch):
    from app.repositories.runs import RunRepository

    created = await app_client.post("/api/runs", json={"goal": "断流恢复测试"})
    run_id = created.json()["run_id"]
    monkeypatch.setattr(runs_api, "SessionLocal", app_client._astra_session)
    async with app_client._astra_session() as session:
        repo = RunRepository(session)
        skipped = await repo.add_event(run_id, "reasoning.summary.delta", {"delta": "旧片段"})
        included = await repo.add_event(run_id, "reasoning.summary.completed", {"summary": "恢复后的摘要"})
        await repo.update_run_status(run_id, "completed", summary="完成")
        await session.commit()

    response = await app_client.get(f"/api/runs/{run_id}/events?after_id={skipped.id}")

    assert response.status_code == 200
    assert f"id: {skipped.id}\n" not in response.text
    assert f"id: {included.id}\n" in response.text
    streamed = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]
    assert any(item.get("payload", {}).get("summary") == "恢复后的摘要" for item in streamed)


async def test_run_task_is_retained_until_background_execution_finishes(app_client, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_runner(run_id, settings):
        started.set()
        await release.wait()

    monkeypatch.setattr(runs_api, "start_run_in_process", delayed_runner)
    created = await app_client.post("/api/runs", json={"goal": "后台任务引用测试"})
    run_id = created.json()["run_id"]
    await started.wait()

    assert any(task.get_name() == f"astra-run-{run_id}" for task in runs_api._background_tasks)
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not any(task.get_name() == f"astra-run-{run_id}" for task in runs_api._background_tasks)


async def test_active_run_can_be_cancelled_idempotently(app_client, monkeypatch):
    started = asyncio.Event()

    async def delayed_runner(run_id, settings):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runs_api, "start_run_in_process", delayed_runner)
    created = await app_client.post("/api/runs", json={"goal": "持续生成回答"})
    run_id = created.json()["run_id"]
    await started.wait()

    cancelled = await app_client.post(f"/api/runs/{run_id}/cancel")
    cancelled_again = await app_client.post(f"/api/runs/{run_id}/cancel")

    assert cancelled.status_code == cancelled_again.status_code == 200
    assert cancelled.json()["status"] == cancelled_again.json()["status"] == "cancelled"
    assert cancelled.json()["terminal_reason"]["category"] == "user_cancelled"
    assert [event["type"] for event in cancelled_again.json()["events"]].count("run.cancelled") == 1
    assert run_id not in runs_api._background_tasks_by_run


async def test_cancel_run_returns_completed_snapshot_and_missing_run_is_404(app_client):
    created = await app_client.post("/api/runs", json={"goal": "已完成任务"})
    run_id = created.json()["run_id"]
    async with app_client._astra_session() as session:
        from app.repositories.runs import RunRepository

        await RunRepository(session).update_run_status(
            run_id, "completed", summary="自然完成", result={"summary": "自然完成"}
        )

    completed = await app_client.post(f"/api/runs/{run_id}/cancel")
    missing = await app_client.post("/api/runs/missing/cancel")

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["summary"] == "自然完成"
    assert missing.status_code == 404


async def test_create_run_compiles_reasoning_policy(app_client):
    created = await app_client.post(
        "/api/runs",
        json={
            "goal": "分析复杂问题",
            "reasoning_policy": {
                "reasoning_effort": "deep",
                "max_tool_calls": 42,
                "planning_strategy": "plan_first",
                "reflection_enabled": False,
                "reflection_trigger": "adaptive",
                "execution_mode": "request_approval",
                "verification_level": "strict",
            },
        },
    )
    run_id = created.json()["run_id"]
    body = (await app_client.get(f"/api/runs/{run_id}")).json()
    assert body["reasoning_policy"]["requested"]["reasoning_effort"] == "deep"
    assert body["reasoning_policy"]["effective"]["budgets"]["max_tool_calls"] == 42
    assert body["reasoning_policy"]["effective"]["budgets"]["max_reflections"] == 6


async def test_resume_requires_waiting_run(app_client):
    created = await app_client.post("/api/runs", json={"goal": "普通任务"})
    response = await app_client.post(
        f"/api/runs/{created.json()['run_id']}/resume", json={"content": "继续"}
    )
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
