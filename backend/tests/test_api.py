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
from app.repositories.plans import PlanRepository, plan_to_view
from app.repositories.runs import RunRepository
from app.runner.planning import PlanService, canonical_agent_state
from app.runner.reasoning import build_default_contract
from app.schemas.agent import ExpectedObservation, PlanDraft, PlanningStrategy, PlanNodeDraft


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
    settings = Settings(
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
    )
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


async def test_unattended_run_requires_permission_bundle(app_client):
    response = await app_client.post(
        "/api/runs", json={"goal": "后台整理", "interactive": False}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PERMISSION_BUNDLE_REQUIRED"


async def test_unattended_run_rejects_unsigned_permission_bundle(app_client):
    response = await app_client.post(
        "/api/runs",
        json={
            "goal": "后台整理",
            "interactive": False,
            "permission_bundle": {
                "id": "forged",
                "version": "1",
                "allowed_actions": ["workspace_write"],
                "allowed_resources": ["*"],
                "allowed_effect_kinds": ["workspace_write"],
                "allowed_tool_identities": ["*"],
                "digest": "hmac-sha256:forged",
            },
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PERMISSION_BUNDLE_INVALID"


async def test_remote_api_clients_are_rejected_by_default():
    app = create_app(Settings(model_provider="mock"))
    transport = ASGITransport(app=app, client=("203.0.113.9", 40000))
    async with AsyncClient(transport=transport, base_url="http://astra") as client:
        response = await client.get("/api/health")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "REMOTE_API_DENIED"


async def test_policy_simulation_reports_shadow_decision_change(app_client):
    payload = {
        "request": {
            "subject": {"agent_id": "agent-1", "task_id": "task-1", "run_id": "run-1"},
            "action": "workspace.file.write",
            "resource": "task://task-1/workspace/report.txt",
        },
        "policies": {
            "version": "1",
            "rules": [{
                "id": "allow", "source": "user", "tier": "user", "decision": "allow",
                "actions": ["workspace.file.write"], "resources": ["task://*/workspace/**"],
                "reason_code": "allowed",
            }],
        },
        "shadow_policies": {
            "version": "2",
            "rules": [{
                "id": "deny", "source": "managed", "tier": "managed", "decision": "deny",
                "actions": ["workspace.file.write"], "resources": ["task://*/workspace/**"],
                "reason_code": "managed_deny",
            }],
        },
    }
    response = await app_client.post("/api/permissions/simulate", json=payload)
    assert response.status_code == 200
    assert response.json()["effective"]["decision"] == "allow"
    assert response.json()["shadow"]["decision"] == "deny"
    assert response.json()["changed"] is True


async def test_workspace_file_view_and_safe_download(app_client):
    from app.repositories.workspaces import WorkspaceRepository
    from app.workspaces.runtime import WorkspaceRuntimeService

    async with app_client._astra_session() as session:
        run = await RunRepository(session).create_task_run("生成文件", {})
        runtime = WorkspaceRuntimeService(
            WorkspaceRepository(session),
            app_client._astra_settings.task_workspace_store_path,
            max_files=100,
            max_bytes=1024 * 1024,
            max_file_bytes=1024 * 1024,
        )
        path = await runtime.prepare(run.task_id)
        before = runtime.scan(path)
        (path / "report.md").write_text("# report", encoding="utf-8")
        await runtime.capture_changes(
            run_id=run.id,
            tool_call_id=None,
            workspace_dir=path,
            before=before,
        )
        task_id = run.task_id

    view = await app_client.get(f"/api/tasks/{task_id}/workspace")
    assert view.status_code == 200
    file = view.json()["files"][0]
    assert file["security_status"] == "verified"
    assert file["content_url"]
    content = await app_client.get(file["content_url"])
    assert content.status_code == 200
    assert content.text == "# report"


async def test_library_lists_present_files_with_conversation_context(app_client):
    from app.repositories.workspaces import WorkspaceRepository
    from app.workspaces.runtime import WorkspaceRuntimeService

    async with app_client._astra_session() as session:
        run = await RunRepository(session).create_task_run("资料库测试", {})
        runtime = WorkspaceRuntimeService(
            WorkspaceRepository(session),
            app_client._astra_settings.task_workspace_store_path,
            max_files=100,
            max_bytes=1024 * 1024,
            max_file_bytes=1024 * 1024,
        )
        path = await runtime.prepare(run.task_id)
        before = runtime.scan(path)
        (path / "library.md").write_text("# library", encoding="utf-8")
        await runtime.capture_changes(run_id=run.id, tool_call_id=None, workspace_dir=path, before=before)

    response = await app_client.get("/api/library/files")
    assert response.status_code == 200
    item = next(file for file in response.json() if file["path"] == "library.md")
    assert item["task_id"] == run.task_id
    assert item["conversation_title"] == "资料库测试"
    assert item["content_url"]


async def test_create_run_rejects_removed_direct_planning_strategy(app_client):
    response = await app_client.post(
        "/api/runs",
        json={"goal": "测试旧策略", "reasoning_policy": {"planning_strategy": "direct"}},
    )

    assert response.status_code == 422


async def test_activate_plan_starts_a_planned_run(app_client):
    async with app_client._astra_session() as session:
        repo = RunRepository(session)
        run = await repo.create_task_run("批准后执行", {"provider": "mock"})
        contract = build_default_contract("批准后执行")
        plan = await PlanService(PlanRepository(session)).create(
            run.id,
            PlanDraft(
                strategy=PlanningStrategy.direct,
                nodes=[
                    PlanNodeDraft(
                        node_key="respond",
                        title="生成回复",
                        intent="回应用户",
                        success_criteria_refs=["criterion-result"],
                        expected_outcome=ExpectedObservation(
                            kind="final_answer", success_condition="answer exists"
                        ),
                    )
                ],
            ),
            contract=contract,
            activate=False,
        )
        state = canonical_agent_state(contract, plan, policy_version=1).model_copy(
            update={"active_plan_id": None}
        )
        await repo.initialize_reasoning_state(
            run.id,
            task_contract=contract.model_dump(mode="json"),
            plan_graph=plan_to_view(plan).model_dump(mode="json"),
            agent_state=state.model_dump(mode="json"),
        )
        await repo.update_run_status(run.id, "completed")
        run_id = run.id

    response = await app_client.post(f"/api/runs/{run_id}/activate-plan")
    assert response.status_code == 200
    assert response.json()["status"] == "executing"
    async with app_client._astra_session() as session:
        loaded = await RunRepository(session).require_run(run_id)
        assert loaded.active_plan_id
        assert loaded.agent_state["active_plan_id"] == loaded.active_plan_id
        assert loaded.completed_at is None


async def test_conversation_detail_eager_loads_canonical_plan(app_client):
    async with app_client._astra_session() as session:
        repo = RunRepository(session)
        run = await repo.create_task_run("读取规范计划对话", {"provider": "mock"})
        contract = build_default_contract("读取规范计划对话")
        plan = await PlanService(PlanRepository(session)).create(
            run.id,
            PlanDraft(
                strategy=PlanningStrategy.direct,
                nodes=[
                    PlanNodeDraft(
                        node_key="respond",
                        title="生成回复",
                        intent="回应用户",
                        success_criteria_refs=["criterion-result"],
                        expected_outcome=ExpectedObservation(
                            kind="final_answer", success_condition="answer exists"
                        ),
                    )
                ],
            ),
            contract=contract,
        )
        state = canonical_agent_state(contract, plan, policy_version=1)
        await repo.initialize_reasoning_state(
            run.id,
            task_contract=contract.model_dump(mode="json"),
            plan_graph=plan_to_view(plan).model_dump(mode="json"),
            agent_state=state.model_dump(mode="json"),
        )
        conversation_id = run.task_id

    response = await app_client.get(f"/api/conversations/{conversation_id}")

    assert response.status_code == 200
    assert response.json()["runs"][0]["plan_graph"]["id"] == plan.id
    assert response.json()["runs"][0]["steps"][0]["node_key"] == "respond"


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
        "web_search",
        "web_fetch",
        "chart_render",
        "bash_execute",
    }

    updated = await app_client.put(
        "/api/tools",
        json={
            "web_search": False,
            "web_fetch": True,
            "chart_render": False,
            "bash_execute": True,
        },
    )
    assert updated.status_code == 200
    states = {tool["name"]: tool["enabled"] for tool in updated.json()["tools"]}
    assert states == {
        "web_search": False,
        "web_fetch": True,
        "chart_render": False,
        "bash_execute": True,
    }
    reloaded = await app_client.get("/api/tools")
    persisted = {tool["name"]: tool["enabled"] for tool in reloaded.json()["tools"]}
    assert persisted == states


async def test_conversation_strategy_can_be_restored_and_updated(app_client):
    loaded = await app_client.get("/api/preferences/conversation-strategy")
    assert loaded.status_code == 200
    assert loaded.json() == {
        "preferred_answer_mode": "standard",
        "reasoning_effort": "balanced",
        "max_tool_calls": 8,
        "planning_strategy": "adaptive",
        "reflection_enabled": True,
        "reflection_trigger": "adaptive",
    }

    updated = {
        "preferred_answer_mode": "trusted",
        "reasoning_effort": "deep",
        "max_tool_calls": None,
        "planning_strategy": "plan_first",
        "reflection_enabled": False,
        "reflection_trigger": "failure_only",
    }
    saved = await app_client.put("/api/preferences/conversation-strategy", json=updated)
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
    assert response.json()["max_tool_calls"] is None


@pytest.mark.parametrize(
    ("effort", "limit"),
    [("fast", 0), ("fast", 5), ("balanced", 6), ("balanced", 15), ("deep", None)],
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
    [("fast", 6), ("balanced", 5), ("balanced", 16), ("deep", 15), ("deep", 50)],
)
async def test_conversation_strategy_rejects_tool_limits_outside_effort_range(
    app_client, effort, limit
):
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
        session.add(
            AgentTurnRecord(
                run_id=run_id,
                turn_index=1,
                decision_type="finalize",
                reasoning_summary="正在整理公开回答",
                status="completed",
            )
        )
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
        {
            "role": "process",
            "content": "",
            "items": [
                {
                    "kind": "reasoning",
                    "title": "思考",
                    "detail": "正在整理公开回答",
                    "status": "completed",
                }
            ],
        },
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
        included = await repo.add_event(
            run_id, "reasoning.summary.completed", {"summary": "恢复后的摘要"}
        )
        await repo.update_run_status(run_id, "completed", summary="完成")
        await session.commit()

    response = await app_client.get(f"/api/runs/{run_id}/events?after_id={skipped.id}")

    assert response.status_code == 200
    assert f"id: {skipped.id}\n" not in response.text
    assert f"id: {included.id}\n" in response.text
    streamed = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
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
            "answer_mode": "trusted",
            "reasoning_policy": {
                "reasoning_effort": "deep",
                "max_tool_calls": None,
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
    assert created.json()["answer_mode"] == "trusted"
    assert body["answer_mode"] == "trusted"
    assert body["execution_profile"]["assurance_level"] == "full"
    assert body["reasoning_policy"]["requested"]["reasoning_effort"] == "deep"
    assert body["reasoning_policy"]["effective"]["budgets"]["max_tool_calls"] is None
    assert body["reasoning_policy"]["effective"]["budgets"]["max_reflections"] == 6


async def test_create_run_defaults_to_standard_profile(app_client):
    created = await app_client.post(
        "/api/runs",
        json={
            "goal": "快速回答",
            "reasoning_policy": {
                "reasoning_effort": "deep",
                "max_tool_calls": None,
                "planning_strategy": "plan_first",
                "reflection_enabled": True,
                "execution_mode": "request_approval",
            },
        },
    )
    body = (await app_client.get(f"/api/runs/{created.json()['run_id']}")).json()
    assert created.json()["answer_mode"] == "standard"
    assert body["answer_mode"] == "standard"
    assert body["execution_profile"]["assurance_level"] == "basic"
    assert body["reasoning_policy"]["effective"]["reasoning_effort"] == "fast"
    assert body["reasoning_policy"]["effective"]["planning_strategy"] == "adaptive"
    assert body["reasoning_policy"]["effective"]["reflection_enabled"] is False
    assert body["reasoning_policy"]["effective"]["budgets"]["max_tool_calls"] is None
    assert body["reasoning_policy"]["effective"]["budgets"]["max_turns"] is None


async def test_tool_approval_decision_api_consumes_token_once(app_client):
    async with app_client._astra_session() as session:
        repo = RunRepository(session)
        run = await repo.create_task_run("批准命令", {"provider": "mock"})
        turn = await repo.create_agent_turn(
            run.id,
            1,
            "call_tool",
            "执行命令",
            selected_tool="bash_execute",
            decision={
                "decision_type": "call_tool",
                "reasoning_summary": "执行命令",
                "tool_name": "bash_execute",
                "tool_input": {"command": "printf ok"},
            },
            phase="prepared",
        )
        call = await repo.start_tool_call(
            run.id,
            None,
            "bash_execute",
            "1.0",
            {"command": "printf ok"},
            "command_execute",
            "external_side_effect",
            status="awaiting_approval",
        )
        await repo.update_agent_turn(turn.id, tool_call_id=call.id, phase="awaiting_approval")
        approval = await repo.create_approval_request(
            run_id=run.id,
            turn_id=turn.id,
            tool_call_id=call.id,
            tool_name="bash_execute",
            tool_version="1.0",
            frozen_input={"command": "printf ok"},
            input_hash="hash",
            preview="printf ok",
            permission="command_execute",
            impact="external_side_effect",
            similar_matcher={"kind": "command_prefix", "tokens": ["printf"]},
        )
        waiting = await repo.set_waiting_state(
            run.id,
            {"kind": "tool_approval", "approval_id": approval.id, "tool_call_id": call.id},
        )
        token = waiting.waiting_state["continuation_token"]
        run_id = run.id

    accepted = await app_client.post(
        f"/api/runs/{run_id}/approvals/{approval.id}/decision",
        json={"decision": "allow_similar", "continuation_token": token},
    )
    replay = await app_client.post(
        f"/api/runs/{run_id}/approvals/{approval.id}/decision",
        json={"decision": "allow_similar", "continuation_token": token},
    )

    assert accepted.status_code == 200
    assert accepted.json()["status"] == "executing"
    assert replay.status_code == 409
    async with app_client._astra_session() as session:
        loaded = await RunRepository(session).require_run(run_id)
        assert loaded.tool_calls[0].status == "approved"
        assert len(loaded.approval_grants) == 1


async def test_create_run_rejects_unknown_model_provider(app_client):
    response = await app_client.post(
        "/api/runs",
        json={
            "goal": "测试模型配置",
            "model": {
                "provider": "unknown-provider",
                "name": "test-model",
                "api_key": "secret",
                "base_url": "https://example.test/v1",
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_PROVIDER_UNSUPPORTED"


@pytest.mark.parametrize("provider", ["anthropic", "google", "azure", "groq", "qwen"])
def test_model_config_accepts_supported_cloud_providers(provider):
    configured = runs_api._apply_model_config(
        Settings(model_provider="mock"),
        {
            "provider": provider,
            "name": "test-model",
            "api_key": "secret",
            "base_url": "https://example.test/v1",
        },
    )

    assert configured.model_provider == provider


@pytest.mark.parametrize("provider", ["ollama", "lmstudio", "vllm", "localai", "compatible"])
def test_model_config_allows_keyless_local_providers(provider):
    configured = runs_api._apply_model_config(
        Settings(model_provider="mock"),
        {
            "provider": provider,
            "name": "local-model",
            "api_key": "",
            "base_url": "http://127.0.0.1:1234/v1",
        },
    )

    assert configured.model_provider == provider


async def test_create_run_rejects_missing_model_base_url(app_client):
    response = await app_client.post(
        "/api/runs",
        json={
            "goal": "测试模型配置",
            "model": {
                "provider": "openai",
                "name": "test-model",
                "api_key": "secret",
                "base_url": "",
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "MODEL_CONFIGURATION_REQUIRED"


async def test_resume_requires_waiting_run(app_client):
    created = await app_client.post("/api/runs", json={"goal": "普通任务"})
    response = await app_client.post(
        f"/api/runs/{created.json()['run_id']}/resume", json={"content": "继续"}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_NOT_WAITING"


async def test_resume_reuses_selected_model_configuration(app_client, monkeypatch):
    created = await app_client.post("/api/runs", json={"goal": "需要补充信息"})
    run_id = created.json()["run_id"]
    async with app_client._astra_session() as session:
        from app.repositories.runs import RunRepository

        await RunRepository(session).set_waiting_state(
            run_id,
            {"request": "请补充", "continuation_token": "resume-token"},
        )

    scheduled = {}
    monkeypatch.setattr(
        runs_api,
        "_schedule_run",
        lambda scheduled_run_id, settings: scheduled.update(
            run_id=scheduled_run_id, settings=settings
        ),
    )
    response = await app_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "content": "补充内容",
            "continuation_token": "resume-token",
            "model": {
                "provider": "compatible",
                "name": "local-model",
                "api_key": "",
                "base_url": "http://127.0.0.1:11434/v1",
            },
        },
    )

    assert response.status_code == 200
    assert scheduled["run_id"] == run_id
    assert scheduled["settings"].model_provider == "compatible"
    assert scheduled["settings"].model_name == "local-model"
    assert scheduled["settings"].model_base_url == "http://127.0.0.1:11434/v1"


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
