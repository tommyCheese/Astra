import asyncio
import json

import pytest
from fake_information_tools import fake_information_registry
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.agent_runtime.policies.reasoning import (
    build_default_contract,
    resolve_run_profile,
)
from app.application.permissions.governance import verify_permission_bundle
from app.application.planning import revision as plan_revision_module
from app.application.planning.service import PlanService, canonical_agent_state
from app.common.core.config import AstraRuntimeSettings, get_settings
from app.common.schemas.agent.planning import ExpectedObservation, PlanDraft, PlanNodeDraft
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.types import AnswerMode, PlanExecution, PlanNodeStatus
from app.common.schemas.permissions import PermissionBundle
from app.domain.memory import MemoryStatus
from app.infrastructure.db.model_base import AstraOrmRecordBase, utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.session import get_session
from app.infrastructure.repositories.approval_contracts import ApprovalRequestCreate
from app.infrastructure.repositories.memories import MemoryRepository
from app.infrastructure.repositories.plans import PlanRepository, plan_to_view
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.interfaces.api import runs as runs_api
from app.interfaces.api.model_providers import get_runtime_default_model
from app.main import create_app


@pytest.fixture
async def app_client(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def override_session():
        async with Session() as session:
            yield session

    async def noop_runner(run_id, settings):
        return None

    monkeypatch.setattr(
        plan_revision_module,
        "build_application_tool_registry",
        lambda settings: fake_information_registry(),
    )
    settings = AstraRuntimeSettings(
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
        runtime_profile_path=str(tmp_path / "runtime-profile.json"),
    )
    app = create_app(settings, session_factory=Session)
    monkeypatch.setattr(app.state.container.run_dispatcher, "_run_starter", noop_runner)
    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        client._astra_session = Session
        client._astra_settings = settings
        client._astra_runtime_service = app.state.container.runtime_profile_service
        client._astra_autodream_service = app.state.container.autodream_service
        client._astra_run_dispatcher = app.state.container.run_dispatcher
        yield client
    await engine.dispose()


async def test_create_run_rejects_empty_goal(app_client):
    response = await app_client.post("/api/runs", json={"goal": " "})

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "GOAL_REQUIRED"
    assert error["type"] == "validation.input_invalid"
    assert error["trace_id"].startswith("req_")


async def test_runtime_default_model_reports_whether_it_is_runnable():
    missing_key = await get_runtime_default_model(
        AstraRuntimeSettings(model_provider="openai", model_name="gpt-5", model_api_key="")
    )
    assert missing_key.model_dump() == {
        "provider": "openai",
        "model": "gpt-5",
        "configured": False,
    }

    local_model = await get_runtime_default_model(
        AstraRuntimeSettings(
            model_provider="ollama",
            model_name="qwen3",
            model_base_url="http://127.0.0.1:11434/v1",
        )
    )
    assert local_model.configured is True

    mock_model = await get_runtime_default_model(AstraRuntimeSettings(model_provider="mock", model_name="mock"))
    assert mock_model.configured is True


async def test_scheduled_tasks_api_is_global_and_versioned(app_client):
    target = await app_client.post(
        "/api/conversations",
        json={"title": "Daily brief results"},
    )
    assert target.status_code == 201
    created = await app_client.post(
        "/api/schedules",
        json={
            "name": "Daily brief",
            "target_task_id": target.json()["id"],
            "prompt": "Summarize updates",
            "schedule": {"type": "cron", "expression": "0 9 * * *"},
            "timezone": "Asia/Shanghai",
            "execution": {"permission_bundle": {"token": "signed"}},
        },
    )
    assert created.status_code == 201
    job = created.json()

    listed = await app_client.get("/api/schedules")
    assert [item["id"] for item in listed.json()] == [job["id"]]
    assert listed.json()[0]["target_task_id"] == target.json()["id"]

    protected_target = await app_client.delete(f"/api/conversations/{target.json()['id']}")
    assert protected_target.status_code == 409
    assert protected_target.json()["error"]["code"] == "CONVERSATION_HAS_AUTOMATIONS"

    paused = await app_client.post(
        f"/api/schedules/{job['id']}/pause",
        json={"version": job["version"]},
    )
    assert paused.status_code == 200
    assert paused.json()["enabled"] is False

    stale = await app_client.patch(
        f"/api/schedules/{job['id']}",
        json={"version": job["version"], "name": "Stale"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "SCHEDULE_VERSION_CONFLICT"


async def test_scheduled_tasks_api_binds_result_conversation_and_resolves_execution(app_client, monkeypatch):
    from app.application.scheduling.execution import ScheduledExecutionResolver
    from app.common.schemas.schedules import ScheduledExecutionConfig

    now = utc_now()
    async with app_client._astra_session() as session:
        task = TaskRecord(
            title="Management page target",
            description="Management page target",
            status="created",
            preferred_answer_mode="standard",
            created_at=now,
            updated_at=now,
        )
        session.add(task)
        await session.commit()
        task_id = task.id

    resolved_sources = []

    async def resolve_execution(self, target_task_id):
        resolved_sources.append(("task", target_task_id))
        return ScheduledExecutionConfig(permission_bundle={"token": "signed"})

    async def resolve_workspace_execution(self):
        resolved_sources.append(("workspace", None))
        return ScheduledExecutionConfig(permission_bundle={"token": "signed"})

    monkeypatch.setattr(ScheduledExecutionResolver, "from_task", resolve_execution)
    monkeypatch.setattr(ScheduledExecutionResolver, "from_workspace", resolve_workspace_execution)
    created = await app_client.post(
        "/api/schedules",
        json={
            "name": "Created in management page",
            "target_task_id": task_id,
            "prompt": "Summarize updates",
            "schedule": {"type": "cron", "expression": "0 9 * * *"},
            "timezone": "Asia/Shanghai",
        },
    )

    assert created.status_code == 201
    assert created.json()["target_task_id"] == task_id
    assert created.json()["execution"]["permission_bundle"] == {"token": "signed"}
    heartbeat = await app_client.put(
        "/api/heartbeat",
        json={
            "target_task_id": task_id,
            "enabled": True,
            "interval_seconds": 1800,
            "timezone": "Asia/Shanghai",
        },
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["kind"] == "heartbeat"
    assert heartbeat.json()["execution"]["permission_bundle"] == {"token": "signed"}
    assert resolved_sources == [("task", task_id), ("task", task_id)]

    rejected_binding = await app_client.post(
        "/api/schedules",
        json={
            "name": "Missing result conversation",
            "prompt": "Should be rejected",
            "target_task_id": "missing-task",
            "schedule": {"type": "cron", "expression": "0 9 * * *"},
            "timezone": "Asia/Shanghai",
            "execution": {"permission_bundle": {"token": "signed"}},
        },
    )
    assert rejected_binding.status_code == 404
    assert rejected_binding.json()["error"]["code"] == "CONVERSATION_NOT_FOUND"


async def test_schedule_management_falls_back_to_signed_model_only_execution(app_client):
    target = await app_client.post(
        "/api/conversations",
        json={"title": "Model-only scheduled result"},
    )

    created = await app_client.post(
        "/api/schedules",
        json={
            "name": "No privileged run required",
            "target_task_id": target.json()["id"],
            "prompt": "Reply with a fixed marker",
            "schedule": {"type": "interval", "interval_seconds": 3600},
            "timezone": "Asia/Shanghai",
        },
    )

    assert created.status_code == 201
    execution = created.json()["execution"]
    bundle = PermissionBundle.model_validate(execution["permission_bundle"])
    assert execution["model"] is None
    assert bundle.allowed_actions == []
    assert bundle.allowed_resources == []
    assert bundle.allowed_effect_kinds == []
    assert bundle.allowed_tool_identities == []
    assert bundle.network_destinations == []
    assert verify_permission_bundle(bundle, app_client._astra_settings.permission_bundle_signing_secret)


async def test_heartbeat_api_uses_one_global_desired_state(app_client):
    now = utc_now()
    async with app_client._astra_session() as session:
        tasks = [
            TaskRecord(
                title=f"Heartbeat target {index}",
                description="Heartbeat target",
                status="created",
                preferred_answer_mode="standard",
                created_at=now,
                updated_at=now,
            )
            for index in range(2)
        ]
        session.add_all(tasks)
        await session.commit()
        task_ids = [task.id for task in tasks]

    base = {
        "enabled": True,
        "interval_seconds": 1800,
        "timezone": "Asia/Shanghai",
        "prompt": "Check explicit pending work; otherwise HEARTBEAT_OK",
        "execution": {"permission_bundle": {"token": "signed"}},
    }
    first = await app_client.put("/api/heartbeat", json={**base, "target_task_id": task_ids[0]})
    second = await app_client.put("/api/heartbeat", json={**base, "target_task_id": task_ids[1]})
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["id"] == first.json()["id"]
    assert second.json()["target_task_id"] == task_ids[1]

    listed = await app_client.get("/api/schedules?kind=heartbeat")
    assert len(listed.json()) == 1
    assert listed.json()[0]["system_managed"] is True


async def test_memory_management_api_lists_details_and_revokes_with_cas(app_client):
    async with app_client._astra_session() as session:
        run_repo = RunUnitOfWork(session)
        source_run = await run_repo.create_task_run(
            "记住数据库",
            {"provider": "mock", "model": "mock"},
        )
        target_run = await run_repo.create_task_run(
            "读取数据库",
            {"provider": "mock", "model": "mock"},
            task_id=source_run.task_id,
        )
        memory = await MemoryRepository(session).create(
            run_id=source_run.id,
            scope="task",
            kind="semantic_fact",
            memory_key="project:database",
            content="项目主数据库是 PostgreSQL。",
            provenance={"run_id": source_run.id},
            confidence=0.95,
        )
        memory_id = memory.id
        target_task_id = target_run.task_id

    listed = await app_client.get(
        "/api/memories",
        params={
            "namespace_type": "task",
            "namespace_id": target_task_id,
            "include_history": "true",
        },
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == memory_id
    assert listed.json()["items"][0]["namespace_id"] == target_task_id

    detail = await app_client.get(f"/api/memories/{memory_id}")
    assert detail.status_code == 200
    assert detail.json()["sources"][0]["run_id"] == source_run.id
    assert detail.json()["history"][0]["memory_key"] == "project:database"
    assert detail.json()["audit_events"][0]["event_type"] == "created"

    stale = await app_client.post(
        f"/api/memories/{memory_id}/revoke",
        json={
            "expected_state_version": 99,
            "reason": "错误信息",
            "actor": "local-test",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "MEMORY_VERSION_CONFLICT"

    revoked = await app_client.post(
        f"/api/memories/{memory_id}/revoke",
        json={
            "expected_state_version": 1,
            "reason": "错误信息",
            "actor": "local-test",
        },
    )
    assert revoked.status_code == 200
    assert revoked.json()["status"] == "revoked"
    assert revoked.json()["state_version"] == 2
    assert revoked.json()["revoke_reason"] == "错误信息"
    assert revoked.json()["audit_events"][-1]["event_type"] == "status_changed"


async def test_memory_management_api_requires_explicit_human_activation(app_client):
    async with app_client._astra_session() as session:
        run = await RunUnitOfWork(session).create_task_run(
            "人工确认记忆",
            {"provider": "mock", "model": "mock"},
        )
        candidate = await MemoryRepository(session).create(
            run_id=run.id,
            scope="run",
            kind="semantic_fact",
            memory_key="review:fact",
            content="这是一条待确认事实。",
            provenance={"run_id": run.id},
            confidence=0.9,
            status=MemoryStatus.candidate,
        )
        memory_id = candidate.id

    default_list = await app_client.get("/api/memories")
    assert all(item["id"] != memory_id for item in default_list.json()["items"])
    pending_list = await app_client.get("/api/memories", params={"status": "candidate"})
    assert pending_list.json()["items"][0]["id"] == memory_id

    stale = await app_client.post(
        f"/api/memories/{memory_id}/activate",
        json={"expected_state_version": 99, "reason": "人工确认", "actor": "local-test"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "MEMORY_VERSION_CONFLICT"

    activated = await app_client.post(
        f"/api/memories/{memory_id}/activate",
        json={"expected_state_version": 1, "reason": "人工确认", "actor": "local-test"},
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "active"
    assert activated.json()["audit_events"][-1]["event_type"] == "human_activated"
    assert activated.json()["audit_events"][-1]["actor"] == "local-test"


async def test_memory_management_api_rejects_incomplete_namespace_and_missing_memory(
    app_client,
):
    incomplete = await app_client.get(
        "/api/memories",
        params={"namespace_type": "workspace"},
    )
    assert incomplete.status_code == 422
    assert incomplete.json()["error"]["code"] == "MEMORY_NAMESPACE_INCOMPLETE"

    missing = await app_client.get("/api/memories/missing")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "MEMORY_NOT_FOUND"


async def test_context_status_and_registered_commands_preserve_history(app_client):
    now = utc_now()
    task = TaskRecord(
        title="长对话",
        description="开始",
        status="created",
        preferred_answer_mode="standard",
        created_at=now,
        updated_at=now,
    )
    async with app_client._astra_session() as session:
        session.add(task)
        await session.flush()
        repository = RunUnitOfWork(session)
        for index in range(6):
            run = await repository.create_task_run(
                f"问题 {index}",
                app_client._astra_settings.model_policy,
                task_id=task.id,
            )
            await repository.update_run_status(
                run.id,
                "completed",
                summary=f"回答 {index}",
                result={"summary": f"回答 {index}"},
            )
        await session.commit()
        task_id = task.id

    catalog = await app_client.get("/api/system-commands")
    assert catalog.status_code == 200
    assert [item["command"] for item in catalog.json()] == [
        "/compact",
        "/clear",
        "/schedule",
        "/heartbeat",
        "/subagent",
    ]
    assert catalog.json()[2]["argument_mode"] == "required"
    assert catalog.json()[0]["argument_mode"] == "optional"
    assert catalog.json()[0]["default_arguments"] == ""
    assert catalog.json()[2]["usage"].startswith("/schedule ")
    assert catalog.json()[2]["side_effect"] == "mixed"
    assert catalog.json()[4]["execution_mode"] == "run"
    assert catalog.json()[4]["argument_mode"] == "required"
    assert catalog.json()[4]["available"] is True

    default_model = await app_client.get("/api/models/default")
    assert default_model.status_code == 200
    assert default_model.json() == {
        "provider": app_client._astra_settings.model_provider,
        "model": app_client._astra_settings.model_name,
        "configured": True,
    }

    capabilities = await app_client.post(
        "/api/models/context-capabilities/resolve",
        json={
            "models": [
                {"provider": "openai", "model": "gpt-5.6-sol"},
                {"provider": "compatible", "model": "private-model"},
            ]
        },
    )
    assert capabilities.status_code == 200
    assert capabilities.json()["capabilities"] == [
        {
            "provider": "openai",
            "model": "gpt-5.6-sol",
            "window_tokens": 1_050_000,
            "max_output_tokens": 128_000,
            "source": "catalog",
            "verified": True,
            "documentation_url": "https://developers.openai.com/api/docs/models/gpt-5.6-sol",
            "capability_version": 2,
        },
        {
            "provider": "compatible",
            "model": "private-model",
            "window_tokens": 131_072,
            "max_output_tokens": None,
            "source": "fallback",
            "verified": False,
            "documentation_url": None,
            "capability_version": 2,
        },
    ]

    status = await app_client.get(
        f"/api/conversations/{task_id}/context",
        params={"provider": "openai", "model": "gpt-5", "draft": "继续"},
    )
    assert status.status_code == 200
    assert status.json()["window_tokens"] == 400_000
    assert status.json()["context_source"] == "catalog"
    assert status.json()["context_verified"] is True
    assert status.json()["context_documentation_url"] == ("https://developers.openai.com/api/docs/models/gpt-5")
    assert status.json()["visible_run_count"] == 6
    assert status.json()["estimated"] is True

    attempted_override = await app_client.get(
        f"/api/conversations/{task_id}/context",
        params={
            "provider": "compatible",
            "model": "private-model",
            "context_mode": "manual",
            "context_window_tokens": 65_536,
            "max_output_tokens": 4_096,
        },
    )
    assert attempted_override.status_code == 200
    assert attempted_override.json()["window_tokens"] == 131_072
    assert attempted_override.json()["max_output_tokens"] is None
    assert attempted_override.json()["context_source"] == "fallback"
    assert attempted_override.json()["context_verified"] is False

    compacted = await app_client.post(
        f"/api/conversations/{task_id}/commands/compact",
        params={"provider": "openai", "model": "gpt-5"},
    )
    assert compacted.status_code == 200
    assert compacted.json()["context"]["summary_active"] is True
    assert compacted.json()["details"] == {
        "folded": 6,
        "retained": 0,
        "model_used": True,
        "direction": "",
    }
    assert compacted.json()["message"] == "当前上下文已完成压缩。"
    assert compacted.json()["user_message"]["content"] == "/compact"
    assert compacted.json()["user_message"]["assistant_content"] == compacted.json()["message"]

    cleared = await app_client.post(
        f"/api/conversations/{task_id}/commands/clear",
        params={"provider": "openai", "model": "gpt-5"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["context"]["visible_run_count"] == 0
    assert cleared.json()["context"]["summary_active"] is False
    assert cleared.json()["user_message"]["assistant_content"] == cleared.json()["message"]

    detail = await app_client.get(f"/api/conversations/{task_id}")
    assert len(detail.json()["runs"]) == 6

    unknown = await app_client.post(
        f"/api/conversations/{task_id}/commands/not-registered",
        params={"provider": "openai", "model": "gpt-5"},
    )
    assert unknown.status_code == 404
    assert unknown.json()["error"]["code"] == "SYSTEM_COMMAND_NOT_FOUND"


async def test_parameterized_automation_commands_are_host_operations(app_client):
    now = utc_now()
    task = TaskRecord(
        title="自动化命令",
        description="自动化命令",
        status="created",
        preferred_answer_mode="standard",
        created_at=now,
        updated_at=now,
    )
    async with app_client._astra_session() as session:
        session.add(task)
        await session.commit()
        task_id = task.id

    schedules = await app_client.post(
        f"/api/conversations/{task_id}/commands/schedule",
        params={"provider": "mock", "model": "mock-model"},
        json={"arguments": "list"},
    )
    assert schedules.status_code == 200
    assert schedules.json()["details"] == {"jobs": []}

    heartbeat = await app_client.post(
        f"/api/conversations/{task_id}/commands/heartbeat",
        params={"provider": "mock", "model": "mock-model"},
        json={"arguments": "status"},
    )
    assert heartbeat.status_code == 200
    assert heartbeat.json()["details"]["heartbeat"] == {
        "configured": False,
        "enabled": False,
    }

    invalid = await app_client.post(
        f"/api/conversations/{task_id}/commands/schedule",
        params={"provider": "mock", "model": "mock-model"},
        json={"arguments": "list --shell nope"},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "SYSTEM_COMMAND_USAGE_INVALID"

    missing = await app_client.post(
        f"/api/conversations/{task_id}/commands/heartbeat",
        params={"provider": "mock", "model": "mock-model"},
        json={"arguments": ""},
    )
    assert missing.status_code == 422
    assert missing.json()["error"]["code"] == "SYSTEM_COMMAND_ARGUMENTS_REQUIRED"

    compact_with_arguments = await app_client.post(
        f"/api/conversations/{task_id}/commands/compact",
        params={"provider": "mock", "model": "mock-model"},
        json={"arguments": "unexpected"},
    )
    assert compact_with_arguments.status_code == 200
    assert compact_with_arguments.json()["details"]["direction"] == "unexpected"
    assert compact_with_arguments.json()["details"]["model_used"] is False
    assert compact_with_arguments.json()["message"] == "当前上下文无需压缩。"
    assert compact_with_arguments.json()["user_message"]["content"] == "/compact unexpected"

    cleared = await app_client.post(
        f"/api/conversations/{task_id}/commands/clear",
        params={"provider": "mock", "model": "mock-model"},
    )
    assert cleared.status_code == 200
    assert cleared.json()["user_message"]["content"] == "/clear"

    conversation = await app_client.get(f"/api/conversations/{task_id}")
    assert [item["command"] for item in conversation.json()["command_messages"]] == [
        "/schedule",
        "/heartbeat",
        "/compact",
        "/clear",
    ]
    assert all(item["assistant_content"] for item in conversation.json()["command_messages"])

    async with app_client._astra_session() as session:
        runs = await session.execute(select(RunRecord).where(RunRecord.task_id == task_id))
        assert list(runs.scalars()) == []


async def test_unattended_run_requires_permission_bundle(app_client):
    response = await app_client.post("/api/runs", json={"goal": "后台整理", "interactive": False})
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
    app = create_app(AstraRuntimeSettings(model_provider="mock"))
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
            "rules": [
                {
                    "id": "allow",
                    "source": "user",
                    "tier": "user",
                    "decision": "allow",
                    "actions": ["workspace.file.write"],
                    "resources": ["task://*/workspace/**"],
                    "reason_code": "allowed",
                }
            ],
        },
        "shadow_policies": {
            "version": "2",
            "rules": [
                {
                    "id": "deny",
                    "source": "managed",
                    "tier": "managed",
                    "decision": "deny",
                    "actions": ["workspace.file.write"],
                    "resources": ["task://*/workspace/**"],
                    "reason_code": "managed_deny",
                }
            ],
        },
    }
    response = await app_client.post("/api/permissions/simulate", json=payload)
    assert response.status_code == 200
    assert response.json()["effective"]["decision"] == "allow"
    assert response.json()["shadow"]["decision"] == "deny"
    assert response.json()["changed"] is True


async def test_workspace_file_view_and_safe_download(app_client):
    from app.application.workspaces.runtime import WorkspaceRuntimeService
    from app.infrastructure.repositories.workspaces import WorkspaceRepository

    async with app_client._astra_session() as session:
        run = await RunUnitOfWork(session).create_task_run("生成文件", {})
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
    preview = await app_client.get(f"{file['content_url']}?inline=true")
    assert content.status_code == 200
    assert content.text == "# report"
    assert preview.status_code == 200
    assert preview.headers["content-disposition"].startswith("inline;")


async def test_library_lists_present_files_with_conversation_context(app_client):
    from app.application.workspaces.runtime import WorkspaceRuntimeService
    from app.infrastructure.repositories.workspaces import WorkspaceRepository

    async with app_client._astra_session() as session:
        run = await RunUnitOfWork(session).create_task_run("资料库测试", {})
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


@pytest.mark.parametrize("strategy", ["direct", "adaptive", "plan_first"])
async def test_create_run_rejects_removed_planning_strategy(app_client, strategy):
    response = await app_client.post(
        "/api/runs",
        json={"goal": "测试旧策略", "reasoning_policy": {"planning_strategy": strategy}},
    )

    assert response.status_code == 422


async def test_create_run_rejects_removed_plan_only_mode(app_client):
    response = await app_client.post(
        "/api/runs",
        json={"goal": "测试旧模式", "reasoning_policy": {"execution_mode": "plan_only"}},
    )
    assert response.status_code == 422


async def test_removed_plan_activation_route_is_absent(app_client):
    response = await app_client.post("/api/runs/legacy/activate-plan")
    assert response.status_code == 404


async def test_plan_confirmation_resume_consumes_bound_token_once(app_client):
    async with app_client._astra_session() as session:
        repo = RunUnitOfWork(session)
        profile = resolve_run_profile(
            AnswerMode.trusted,
            RequestedReasoningPolicy(),
            plan_execution=PlanExecution.confirm,
        )
        run = await repo.create_task_run(
            "确认计划",
            {"provider": "mock"},
            reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
            answer_mode="trusted",
            execution_profile=profile.model_dump(mode="json"),
        )
        contract = build_default_contract("确认计划")
        plan = await PlanService(PlanRepository(session)).create(
            run.id,
            PlanDraft(
                nodes=[
                    PlanNodeDraft(
                        node_key="respond",
                        title="生成回复",
                        intent="回应用户",
                        success_criteria_refs=["criterion-result"],
                        expected_outcome=ExpectedObservation(kind="final_answer", success_condition="answer exists"),
                    )
                ],
            ),
            contract=contract,
            activate=False,
        )
        state = canonical_agent_state(contract, plan, policy_version=2).model_copy(update={"active_plan_id": None})
        await repo.initialize_reasoning_state(
            run.id,
            task_contract=contract.model_dump(mode="json"),
            plan_graph=plan_to_view(plan).model_dump(mode="json"),
            agent_state=state.model_dump(mode="json"),
        )
        waiting = await repo.set_waiting_state(
            run.id,
            {
                "kind": "plan_confirmation",
                "plan_id": plan.id,
                "plan_version": plan.version,
                "state_version": state.version,
                "request": "确认执行",
            },
        )
        token = waiting.waiting_state["continuation_token"]
        run_id = run.id
        payload = {
            "action": "execute_plan",
            "continuation_token": token,
            "plan_id": plan.id,
            "expected_plan_version": plan.version,
            "expected_state_version": state.version,
        }
        await repo.commit()

    confirmed = await app_client.post(f"/api/runs/{run_id}/resume", json=payload)
    replay = await app_client.post(f"/api/runs/{run_id}/resume", json=payload)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "executing"
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "PLAN_CONFIRMATION_INVALID"


async def _create_waiting_confirmation(
    app_client,
    goal: str = "调整计划",
    model_policy: dict | None = None,
):
    async with app_client._astra_session() as session:
        repo = RunUnitOfWork(session)
        profile = resolve_run_profile(
            AnswerMode.trusted,
            RequestedReasoningPolicy(),
            plan_execution=PlanExecution.confirm,
        )
        run = await repo.create_task_run(
            goal,
            model_policy or {"provider": "mock"},
            reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
            answer_mode="trusted",
            execution_profile=profile.model_dump(mode="json"),
        )
        contract = build_default_contract(goal)
        plan = await PlanService(PlanRepository(session)).create(
            run.id,
            PlanDraft(
                nodes=[
                    PlanNodeDraft(
                        node_key="respond",
                        title="生成回复",
                        intent="回应用户",
                        success_criteria_refs=["criterion-result"],
                        expected_outcome=ExpectedObservation(kind="final_answer", success_condition="answer exists"),
                    )
                ],
            ),
            contract=contract,
            activate=False,
        )
        state = canonical_agent_state(contract, plan, policy_version=2).model_copy(update={"active_plan_id": None})
        await repo.initialize_reasoning_state(
            run.id,
            task_contract=contract.model_dump(mode="json"),
            plan_graph=plan_to_view(plan).model_dump(mode="json"),
            agent_state=state.model_dump(mode="json"),
        )
        waiting = await repo.set_waiting_state(
            run.id,
            {
                "kind": "plan_confirmation",
                "plan_id": plan.id,
                "plan_version": plan.version,
                "state_version": state.version,
                "request": "确认执行",
            },
        )
        payload = {
            "action": "revise_plan",
            "content": "增加资料核验并允许并行搜索。",
            "continuation_token": waiting.waiting_state["continuation_token"],
            "plan_id": plan.id,
            "expected_plan_version": plan.version,
            "expected_state_version": state.version,
        }
        await repo.commit()
        return run.id, plan.id, payload


async def test_plan_revision_creates_new_waiting_version_and_rejects_replay(app_client):
    run_id, original_plan_id, payload = await _create_waiting_confirmation(app_client)

    revised = await app_client.post(f"/api/runs/{run_id}/resume", json=payload)
    replay = await app_client.post(f"/api/runs/{run_id}/resume", json=payload)
    current = await app_client.get(f"/api/runs/{run_id}")
    versions = await app_client.get(f"/api/runs/{run_id}/plans")

    assert revised.status_code == 200
    assert revised.json()["status"] == "waiting_user"
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "PLAN_REVISION_STALE"
    run = current.json()
    assert run["plan_graph"]["version"] == 2
    assert run["plan_graph"]["supersedes_plan_id"] == original_plan_id
    assert run["waiting_state"]["plan_id"] == run["plan_graph"]["id"]
    assert run["waiting_state"]["continuation_token"] != payload["continuation_token"]
    assert [item["status"] for item in versions.json()] == ["superseded", "planned"]
    revised_intent = run["plan_graph"]["nodes"][0]["intent"]
    assert run["task_contract"]["original_goal"] in revised_intent
    assert "revision_request" not in revised_intent
    assert "validation_constraints" not in revised_intent

    confirmed = await app_client.post(
        f"/api/runs/{run_id}/resume",
        json={
            "action": "execute_plan",
            "continuation_token": run["waiting_state"]["continuation_token"],
            "plan_id": run["waiting_state"]["plan_id"],
            "expected_plan_version": run["waiting_state"]["plan_version"],
            "expected_state_version": run["waiting_state"]["state_version"],
        },
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "executing"


async def test_invalid_plan_revision_restores_original_with_fresh_token(app_client, monkeypatch):
    run_id, original_plan_id, payload = await _create_waiting_confirmation(app_client)

    class InvalidRevisionClient:
        def bind_agent_profile(self, profile):
            return None

        def bind_reasoning_effort(self, effort):
            return None

        def bind_model_thinking(self, thinking):
            return None

        async def plan(self, goal, *, contract):
            return PlanDraft(
                nodes=[
                    PlanNodeDraft(
                        node_key="cycle",
                        title="非法循环",
                        intent="触发验证",
                        depends_on=["cycle"],
                        success_criteria_refs=["criterion-result"],
                        expected_outcome=ExpectedObservation(kind="invalid", success_condition="never"),
                    )
                ]
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(
        plan_revision_module,
        "build_model_client",
        lambda settings: InvalidRevisionClient(),
    )
    rejected = await app_client.post(f"/api/runs/{run_id}/resume", json=payload)
    current = (await app_client.get(f"/api/runs/{run_id}")).json()

    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "PLAN_REVISION_INVALID"
    assert current["plan_graph"]["id"] == original_plan_id
    assert current["waiting_state"]["plan_id"] == original_plan_id
    assert current["waiting_state"]["continuation_token"] != payload["continuation_token"]
    assert len(current["plan_versions"]) == 1
    serialized_events = json.dumps(current["events"], ensure_ascii=False)
    assert payload["content"] not in serialized_events


async def test_plan_revision_repairs_one_invalid_model_draft(app_client, monkeypatch):
    run_id, _original_plan_id, payload = await _create_waiting_confirmation(app_client)

    class RepairingRevisionClient:
        def __init__(self):
            self.goals = []

        def bind_agent_profile(self, profile):
            return None

        def bind_reasoning_effort(self, effort):
            return None

        def bind_model_thinking(self, thinking):
            return None

        async def plan(self, goal, *, contract):
            self.goals.append(json.loads(goal))
            depends_on = ["revised"] if len(self.goals) == 1 else []
            capabilities = ["invented_capability"] if len(self.goals) == 1 else ["information.search"]
            return PlanDraft(
                nodes=[
                    PlanNodeDraft(
                        node_key="revised",
                        title="修正后的计划",
                        intent="按调整要求完成目标",
                        depends_on=depends_on,
                        required_capabilities=capabilities,
                        success_criteria_refs=["invented-criterion"],
                        expected_outcome=ExpectedObservation(kind="step_result", success_condition="目标完成"),
                    )
                ]
            )

        async def aclose(self):
            return None

    client = RepairingRevisionClient()
    monkeypatch.setattr(
        plan_revision_module,
        "build_model_client",
        lambda settings: client,
    )

    revised = await app_client.post(f"/api/runs/{run_id}/resume", json=payload)
    current = (await app_client.get(f"/api/runs/{run_id}")).json()

    assert revised.status_code == 200
    assert len(client.goals) == 2
    assert "validation_feedback" in client.goals[1]
    assert current["plan_graph"]["version"] == 2
    node = current["plan_graph"]["nodes"][0]
    assert node["required_capabilities"] == ["information.search"]
    assert node["success_criteria_refs"] == ["criterion-result"]


async def test_plan_revision_reuses_frozen_thinking_and_records_usage(app_client, monkeypatch):
    thinking_snapshot = {
        "requested": {
            "enabled": True,
            "depth": "high",
            "capability_version": 2,
        },
        "effective": {"enabled": True, "depth": "high"},
        "source": "explicit_model_control",
        "adapter": "openai-gpt5-modern",
        "adjustments": [],
        "capability_version": 2,
    }
    run_id, _original_plan_id, payload = await _create_waiting_confirmation(
        app_client,
        model_policy={
            "provider": "openai",
            "model": "gpt-5.6",
            "base_url": "https://user:secret@example.test/v1",
            "thinking": thinking_snapshot,
        },
    )
    payload["model"] = {
        "provider": "openai",
        "name": "gpt-5.6",
        "api_key": "runtime-secret",
        "base_url": "https://example.test/v1",
        "thinking": {
            "enabled": True,
            "depth": "high",
            "capability_version": 2,
        },
    }

    class RevisionSpyClient:
        def __init__(self):
            self.usage_recorder = None
            self.thinking = None
            self.closed = False

        def bind_agent_profile(self, profile):
            return None

        def bind_reasoning_effort(self, effort):
            return None

        def bind_model_thinking(self, thinking):
            self.thinking = thinking

        async def plan(self, goal, *, contract):
            return PlanDraft(
                nodes=[
                    PlanNodeDraft(
                        node_key="respond",
                        title="生成修订回复",
                        intent="按修订要求回应用户",
                        success_criteria_refs=["criterion-result"],
                        expected_outcome=ExpectedObservation(
                            kind="final_answer",
                            success_condition="answer exists",
                        ),
                    )
                ]
            )

        async def aclose(self):
            self.closed = True

    client = RevisionSpyClient()
    monkeypatch.setattr(
        plan_revision_module,
        "build_model_client",
        lambda settings: client,
    )

    revised = await app_client.post(f"/api/runs/{run_id}/resume", json=payload)

    assert revised.status_code == 200
    assert client.thinking == thinking_snapshot
    assert client.usage_recorder.run_id == run_id
    assert client.closed is True


async def test_conversation_detail_eager_loads_canonical_plan(app_client):
    async with app_client._astra_session() as session:
        repo = RunUnitOfWork(session)
        run = await repo.create_task_run("读取规范计划对话", {"provider": "mock"}, answer_mode="trusted")
        contract = build_default_contract("读取规范计划对话")
        plan = await PlanService(PlanRepository(session)).create(
            run.id,
            PlanDraft(
                nodes=[
                    PlanNodeDraft(
                        node_key="respond",
                        title="生成回复",
                        intent="回应用户",
                        success_criteria_refs=["criterion-result"],
                        expected_outcome=ExpectedObservation(kind="final_answer", success_condition="answer exists"),
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
        await repo.commit()

    response = await app_client.get(f"/api/conversations/{conversation_id}")

    assert response.status_code == 200
    assert response.json()["preferred_answer_mode"] == "trusted"
    assert response.json()["runs"][0]["plan_graph"]["id"] == plan.id
    assert response.json()["runs"][0]["steps"][0]["node_key"] == "respond"


async def test_create_run_rejects_invalid_agent_profile_as_configuration_error(app_client, monkeypatch):
    from app.domain.agent_profile import AgentProfileConfigurationError

    def invalid_profile():
        raise AgentProfileConfigurationError("invalid test profile")

    monkeypatch.setattr("app.application.run_management.lifecycle.creation.load_agent_profile", invalid_profile)
    response = await app_client.post("/api/runs", json={"goal": "Profile 配置测试"})

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AGENT_PROFILE_INVALID"
    assert "invalid test profile" not in response.text


async def test_runtime_agent_profile_update_is_used_by_new_runs_and_can_reset(app_client):
    loaded = await app_client.get("/api/runtime")
    assert loaded.status_code == 200
    original = loaded.json()["agent_profile"]
    assert original["source"] == "default"

    documents = dict(original["documents"])
    marker = "Astra Runtime Profile API test"
    documents["identity"] = documents["identity"].replace("# Astra Identity", f"# Astra Identity\n\n{marker}")
    updated = await app_client.put("/api/runtime/agent-profile", json={"documents": documents})
    assert updated.status_code == 200
    assert updated.json()["source"] == "user"
    assert updated.json()["version"] != original["version"]
    assert updated.json()["default_documents"] == original["documents"]

    created = await app_client.post("/api/runs", json={"goal": "Profile 快照测试"})
    async with app_client._astra_session() as session:
        run = await session.get(RunRecord, created.json()["run_id"])
        assert marker in run.agent_profile_snapshot["documents"]["identity"]["content"]

    invalid_documents = dict(documents)
    invalid_documents["identity"] = "invalid"
    rejected = await app_client.put("/api/runtime/agent-profile", json={"documents": invalid_documents})
    assert rejected.status_code == 422
    assert (await app_client.get("/api/runtime")).json()["agent_profile"]["version"] == updated.json()["version"]

    reset = await app_client.post("/api/runtime/agent-profile/reset")
    assert reset.status_code == 200
    assert reset.json()["source"] == "default"
    assert reset.json()["version"] == original["version"]


async def test_runtime_memory_settings_update_controls_autodream(app_client, monkeypatch):
    initial = (await app_client.get("/api/runtime")).json()["memory_settings"]
    calls = []

    async def startup():
        calls.append("startup")

    async def shutdown():
        calls.append("shutdown")

    monkeypatch.setattr(app_client._astra_autodream_service, "startup", startup)
    monkeypatch.setattr(app_client._astra_autodream_service, "shutdown", shutdown)
    enabled = {
        **initial,
        "recall_enabled": True,
        "retrieval_max_items": 4,
        "autodream_enabled": True,
    }
    response = await app_client.put("/api/runtime/memory-settings", json=enabled)
    assert response.status_code == 200
    assert response.json() == enabled
    assert calls == ["startup"]
    assert app_client._astra_settings.agent_memory_cross_session_enabled is True

    disabled = {**enabled, "autodream_enabled": False}
    response = await app_client.put("/api/runtime/memory-settings", json=disabled)
    assert response.status_code == 200
    assert calls == ["startup", "shutdown"]

    invalid = await app_client.put(
        "/api/runtime/memory-settings",
        json={**disabled, "retrieval_min_confidence": 2},
    )
    assert invalid.status_code == 422
    assert app_client._astra_runtime_service.memory_settings() == disabled

    legacy = {**disabled, "cross_session_mode": "on"}
    legacy.pop("recall_enabled")
    rejected = await app_client.put("/api/runtime/memory-settings", json=legacy)
    assert rejected.status_code == 422
    assert app_client._astra_runtime_service.memory_settings() == disabled


async def test_tool_settings_can_be_read_and_updated(app_client):
    loaded = await app_client.get("/api/tools")
    assert loaded.status_code == 200
    expected_tools = {
        "chart.render",
        "bash_execute",
        "forget",
        "remember",
        "swarm",
        "workspace.edit",
        "workspace.list",
        "workspace.read",
        "workspace.search",
        "workspace.write",
    }
    assert {tool["name"] for tool in loaded.json()["tools"]} == expected_tools
    assert {provider["provider_id"] for provider in loaded.json()["providers"]} == {
        "astra.builtin",
        "astra.chart",
        "astra.shell",
    }
    initial_swarm = next(tool for tool in loaded.json()["tools"] if tool["name"] == "swarm")
    assert initial_swarm["available"] is True
    assert initial_swarm["unavailable_reason"] is None

    updated = None
    for name, enabled in {
        "chart.render": False,
        "bash_execute": True,
        "swarm": False,
    }.items():
        updated = await app_client.put(f"/api/tools/{name}/state", json={"enabled": enabled})
        assert updated.status_code == 200
    assert updated is not None
    states = {tool["name"]: tool["enabled"] for tool in updated.json()["tools"]}
    assert states == {
        name: ({"chart.render": False, "bash_execute": True, "swarm": False}.get(name, True)) for name in expected_tools
    }
    removed_legacy = await app_client.put("/api/tools", json={"swarm": True})
    assert removed_legacy.status_code == 405
    reloaded = await app_client.get("/api/tools")
    persisted = {tool["name"]: tool["enabled"] for tool in reloaded.json()["tools"]}
    assert persisted == states


async def test_dynamic_tool_and_provider_settings_validate_identity_and_persist(app_client):
    unknown_tool = await app_client.put("/api/tools/not-a-tool/state", json={"enabled": False})
    assert unknown_tool.status_code == 404
    unknown_provider = await app_client.put("/api/tool-providers/not-a-provider/state", json={"enabled": False})
    assert unknown_provider.status_code == 404

    disabled = await app_client.put("/api/tool-providers/astra.shell/state", json={"enabled": False})
    assert disabled.status_code == 200
    shell = next(provider for provider in disabled.json()["providers"] if provider["provider_id"] == "astra.shell")
    assert shell["enabled"] is False
    assert shell["state"] == "disabled"

    tool_disabled = await app_client.put("/api/tools/chart.render/state", json={"enabled": False})
    assert tool_disabled.status_code == 200
    reloaded = await app_client.get("/api/tools")
    chart = next(tool for tool in reloaded.json()["tools"] if tool["name"] == "chart.render")
    assert chart["enabled"] is False


async def test_retired_web_identities_are_unknown(app_client):
    search = await app_client.put("/api/tools/web_search/state", json={"enabled": True})
    fetch = await app_client.put("/api/tools/web_fetch/state", json={"enabled": True})
    provider = await app_client.put("/api/tool-providers/astra.web/state", json={"enabled": True})
    assert search.status_code == fetch.status_code == provider.status_code == 404


async def test_disabling_swarm_hides_command_and_freezes_subagents_off(app_client):
    settings = app_client._astra_settings
    settings.agent_subagent_rollout_cohort = "trusted_read_only"

    enabled_catalog = await app_client.get("/api/system-commands")
    enabled_command = next(item for item in enabled_catalog.json() if item["name"] == "subagent")
    assert enabled_command["available"] is True

    updated = await app_client.put("/api/tools/swarm/state", json={"enabled": False})
    swarm = next(item for item in updated.json()["tools"] if item["name"] == "swarm")
    assert swarm["enabled"] is False
    assert swarm["available"] is True

    disabled_catalog = await app_client.get("/api/system-commands")
    disabled_command = next(item for item in disabled_catalog.json() if item["name"] == "subagent")
    assert disabled_command["available"] is False
    assert disabled_command["unavailable_reason"] == "Swarm / 子 Agent 工具已由用户关闭。"

    required = await app_client.post(
        "/api/runs",
        json={
            "goal": "必须使用子 Agent",
            "answer_mode": "trusted",
            "plan_execution": "auto",
            "subagent_mode": "required",
        },
    )
    assert required.status_code == 422
    assert required.json()["error"]["code"] == "SUBAGENT_COMMAND_UNAVAILABLE"

    ordinary = await app_client.post(
        "/api/runs",
        json={"goal": "普通可信运行", "answer_mode": "trusted"},
    )
    assert ordinary.status_code == 200
    snapshot = (await app_client.get(f"/api/runs/{ordinary.json()['run_id']}")).json()
    assert snapshot["reasoning_policy"]["effective"]["subagents"]["enabled"] is False

    reenabled = await app_client.put("/api/tools/swarm/state", json={"enabled": True})
    assert reenabled.status_code == 200
    reenabled_catalog = await app_client.get("/api/system-commands")
    reenabled_command = next(item for item in reenabled_catalog.json() if item["name"] == "subagent")
    assert reenabled_command["available"] is True

    required_after_enable = await app_client.post(
        "/api/runs",
        json={
            "goal": "重新启用子 Agent",
            "answer_mode": "trusted",
            "plan_execution": "auto",
            "subagent_mode": "required",
        },
    )
    assert required_after_enable.status_code == 200


async def test_conversation_strategy_can_be_restored_and_updated(app_client):
    loaded = await app_client.get("/api/preferences/conversation-strategy")
    assert loaded.status_code == 200
    assert loaded.json() == {
        "preferred_answer_mode": "standard",
        "reasoning_effort": "balanced",
        "max_tool_calls": 8,
        "reflection_enabled": True,
        "reflection_trigger": "adaptive",
    }

    updated = {
        "preferred_answer_mode": "trusted",
        "reasoning_effort": "deep",
        "max_tool_calls": None,
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
async def test_conversation_strategy_rejects_tool_limits_outside_effort_range(app_client, effort, limit):
    response = await app_client.put(
        "/api/preferences/conversation-strategy",
        json={
            "reasoning_effort": effort,
            "max_tool_calls": limit,
            "reflection_enabled": True,
            "reflection_trigger": "adaptive",
        },
    )
    assert response.status_code == 422


async def test_new_run_uses_persisted_tool_settings(app_client, monkeypatch):
    captured = []

    async def capture_runner(run_id, settings):
        captured.append(settings)

    monkeypatch.setattr(app_client._astra_run_dispatcher, "_run_starter", capture_runner)
    for name, enabled in {
        "chart.render": False,
        "bash_execute": True,
    }.items():
        await app_client.put(f"/api/tools/{name}/state", json={"enabled": enabled})
    created = await app_client.post("/api/runs", json={"goal": "使用持久化工具设置"})
    assert created.status_code == 200
    await asyncio.sleep(0)
    assert captured[0].tool_states["chart.render"] is False
    assert captured[0].tool_states["bash_execute"] is True


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
    response = await app_client.post("/api/runtime/build", json={"dependencies": [{"name": "polars"}]})

    assert response.status_code == 200
    assert captured == [{"name": "polars", "version": ""}]


async def test_artifact_content_enforces_workspace_scope_without_leaking_storage_key(app_client, tmp_path):
    from app.application.workspaces.artifacts import LocalArtifactStore
    from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

    source = tmp_path / "chart.png"
    source.write_bytes(b"\x89PNG\r\n\x1a\nmock")
    store = LocalArtifactStore(app_client._astra_settings.artifact_store_path)
    key = store.put(source, ".png")
    async with app_client._astra_session() as session:
        repo = RunUnitOfWork(session)
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
    assert body["agent_profile"]["version"].startswith("profile-")
    assert "content" not in body["agent_profile"]["documents"]["identity"]
    assert "secret" not in str(body["agent_profile"]).lower()


async def test_run_api_preserves_grounding_result_and_legacy_defaults(app_client):
    created = await app_client.post("/api/runs", json={"goal": "引用测试"})
    run_id = created.json()["run_id"]
    async with app_client._astra_session() as session:
        repository = RunUnitOfWork(session)
        await repository.update_run_status(
            run_id,
            "completed",
            summary="有证据的回答",
            result={
                "summary": "有证据的回答",
                "claims": [
                    {
                        "id": "claim-1",
                        "text": "有证据的回答",
                        "evidence_refs": ["evidence-1"],
                        "support_status": "supported",
                    }
                ],
                "citations": [
                    {
                        "id": "citation-1",
                        "claim_id": "claim-1",
                        "evidence_ref": "evidence-1",
                        "url": "https://example.com/source",
                    }
                ],
                "audit_refs": {
                    "evidence_ledger_artifact_id": "artifact-1",
                    "evidence_record_count": 2,
                },
            },
        )
        await repository.commit()

    grounded = (await app_client.get(f"/api/runs/{run_id}")).json()["result"]
    assert grounded["claims"][0]["evidence_refs"] == ["evidence-1"]
    assert grounded["citations"][0]["claim_id"] == "claim-1"
    assert grounded["audit_refs"]["evidence_record_count"] == 2

    legacy = await app_client.post("/api/runs", json={"goal": "历史结果"})
    legacy_run_id = legacy.json()["run_id"]
    async with app_client._astra_session() as session:
        repository = RunUnitOfWork(session)
        await repository.update_run_status(
            legacy_run_id,
            "completed",
            result={"summary": "历史回答"},
        )
        await repository.commit()
    historical = (await app_client.get(f"/api/runs/{legacy_run_id}")).json()["result"]
    assert historical["claims"] == []
    assert historical["citations"] == []
    assert historical["audit_refs"]["evidence_record_count"] == 0


async def test_conversation_management_and_share_lifecycle(app_client):
    from app.infrastructure.db.models.runs import AgentTurnRecord
    from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

    created = await app_client.post("/api/runs", json={"goal": "需要安全分享的对话"})
    conversation_id = created.json()["task_id"]
    run_id = created.json()["run_id"]
    async with app_client._astra_session() as session:
        await RunUnitOfWork(session).update_run_status(run_id, "completed", summary="公开回答")
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
        f"/api/conversations/{conversation_id}",
        json={
            "title": "用户标题",
            "pinned": True,
            "preferred_answer_mode": "trusted",
        },
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "用户标题"
    assert renamed.json()["title_source"] == "user"
    assert renamed.json()["pinned_at"] is not None
    assert renamed.json()["preferred_answer_mode"] == "trusted"

    listed = await app_client.get("/api/conversations")
    assert listed.json()[0]["id"] == conversation_id
    assert listed.json()[0]["title"] == "用户标题"
    assert listed.json()[0]["preferred_answer_mode"] == "trusted"

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
    from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

    created = await app_client.post("/api/runs", json={"goal": "流连接测试"})
    run_id = created.json()["run_id"]
    monkeypatch.setattr(runs_api, "SessionLocal", app_client._astra_session)
    async with app_client._astra_session() as session:
        await RunUnitOfWork(session).update_run_status(run_id, "completed", summary="完成")
        await session.commit()

    response = await app_client.get(f"/api/runs/{run_id}/events")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert '"type": "stream.ready"' in response.text


async def test_create_run_stream_returns_identity_before_starting_engine(app_client, monkeypatch):
    from app.application.run_management.lifecycle.commands import RunApplicationService
    from app.common.schemas.agent.api_views import CreateRunRequest

    scheduled: list[str] = []

    async def record_start(run_id, _settings):
        scheduled.append(run_id)

    monkeypatch.setattr(app_client._astra_run_dispatcher, "_run_starter", record_start)
    async with app_client._astra_session() as session:
        service = RunApplicationService(
            session,
            app_client._astra_settings,
            app_client._astra_run_dispatcher,
        )
        response = await runs_api.create_run_stream(
            CreateRunRequest(goal="单连接流式创建"),
            session=session,
            service=service,
        )
        ready = json.loads((await anext(response.body_iterator)).removeprefix("data: "))
        assert scheduled == []
        await response.body_iterator.aclose()

    assert ready["type"] == "stream.ready"
    assert ready["payload"]["run_id"]
    assert ready["payload"]["task_id"]
    assert ready["payload"]["status"] == "created"
    assert ready["payload"]["answer_mode"] == "standard"
    assert scheduled == [ready["payload"]["run_id"]]


async def test_run_event_stream_unsubscribes_when_closed_after_ready(app_client, monkeypatch):
    from app.application.run_management.projections.events import RunEventBroker

    created = await app_client.post("/api/runs", json={"goal": "流断连测试"})
    run_id = created.json()["run_id"]
    broker = RunEventBroker()
    monkeypatch.setattr(runs_api, "run_event_broker", broker)

    async with app_client._astra_session() as session:
        response = await runs_api.stream_run_events(run_id, session=session)
        assert '"type": "stream.ready"' in await anext(response.body_iterator)
        assert run_id in broker._states
        await response.body_iterator.aclose()

    assert run_id not in broker._states


async def test_run_event_stream_delivers_committed_broker_event_without_second_query(
    monkeypatch,
):
    from app.application.run_management.projections.events import PublishedRunEvent, RunEventBroker

    query_count = 0

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeRepository:
        TERMINAL_STATUSES = RunUnitOfWork.TERMINAL_STATUSES

        def __init__(self, _session):
            pass

        async def list_events_with_status(self, _run_id, _after_id):
            nonlocal query_count
            query_count += 1
            return [], "executing"

    broker = RunEventBroker()
    monkeypatch.setattr(runs_api, "run_event_broker", broker)
    monkeypatch.setattr(runs_api, "SessionLocal", FakeSession)
    monkeypatch.setattr(runs_api, "RunUnitOfWork", FakeRepository)
    stream = runs_api._run_event_stream("run-live")
    assert '"type": "stream.ready"' in await anext(stream)
    pending = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)

    broker.publish_events(
        [
            PublishedRunEvent(
                id=1,
                run_id="run-live",
                type="answer.delta",
                payload={"delta": "即时片段"},
                created_at="2026-07-29T00:00:00+00:00",
            )
        ]
    )

    streamed = await asyncio.wait_for(pending, timeout=0.05)
    assert '"type": "answer.delta"' in streamed
    assert '"id": 1' in streamed
    assert query_count == 1
    await stream.aclose()


async def test_new_run_engine_gets_scheduled_before_event_replay(monkeypatch):
    timeline: list[str] = []
    engine_tasks: set[asyncio.Task[None]] = set()

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeRepository:
        TERMINAL_STATUSES = RunUnitOfWork.TERMINAL_STATUSES

        def __init__(self, _session):
            pass

        async def list_events_with_status(self, _run_id, _after_id):
            timeline.append("event_replay")
            return [], "completed"

    async def start_engine():
        timeline.append("engine")

    def schedule_engine():
        task = asyncio.create_task(start_engine())
        engine_tasks.add(task)
        task.add_done_callback(engine_tasks.discard)

    monkeypatch.setattr(runs_api, "SessionLocal", FakeSession)
    monkeypatch.setattr(runs_api, "RunUnitOfWork", FakeRepository)
    stream = runs_api._run_event_stream(
        "new-run",
        start_after_ready=schedule_engine,
    )

    assert '"type": "stream.ready"' in await anext(stream)
    assert '"type": "heartbeat"' in await anext(stream)
    assert timeline == ["engine", "event_replay"]
    await stream.aclose()


async def test_run_event_stream_resumes_after_event_id(app_client, monkeypatch):
    from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

    created = await app_client.post("/api/runs", json={"goal": "断流恢复测试"})
    run_id = created.json()["run_id"]
    monkeypatch.setattr(runs_api, "SessionLocal", app_client._astra_session)
    async with app_client._astra_session() as session:
        repo = RunUnitOfWork(session)
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


async def test_fast_sse_replay_and_terminal_snapshot_are_runtime_explicit(app_client, monkeypatch):
    created = await app_client.post("/api/runs", json={"goal": "Fast SSE"})
    run_id = created.json()["run_id"]
    monkeypatch.setattr(runs_api, "SessionLocal", app_client._astra_session)
    async with app_client._astra_session() as session:
        repo = RunUnitOfWork(session)
        cursor = await repo.add_event(run_id, "fast.started", {"runtime": "fast-v1"})
        delta = await repo.add_event(run_id, "answer.delta", {"delta": "首个片段"})
        await repo.add_event(
            run_id,
            "fast.completed",
            {"status": "completed", "runtime": "fast-v1", "runtime_version": 1},
        )
        await repo.update_run_status(
            run_id,
            "completed",
            summary="首个片段",
            result={
                "summary": "首个片段",
                "verification_report": None,
                "completion_decision": None,
            },
        )
        await session.commit()

    response = await app_client.get(f"/api/runs/{run_id}/events?after_id={cursor.id}")
    view = (await app_client.get(f"/api/runs/{run_id}")).json()

    assert f"id: {cursor.id}\n" not in response.text
    assert f"id: {delta.id}\n" in response.text
    assert '"type": "fast.completed"' in response.text
    assert view["runtime_kind"] == "fast-v1"
    assert view["status"] == "completed"
    assert view["result"]["verification_report"] is None
    assert view["result"]["completion_decision"] is None


async def test_plan_graph_events_replay_in_order_without_sensitive_failure_data(app_client, monkeypatch):
    monkeypatch.setattr(runs_api, "SessionLocal", app_client._astra_session)
    async with app_client._astra_session() as session:
        repo = RunUnitOfWork(session)
        run = await repo.create_task_run("图事件回放", {"provider": "mock"}, answer_mode="trusted")
        plan = await PlanRepository(session).create(
            run.id,
            PlanDraft(
                nodes=[
                    PlanNodeDraft(
                        node_key="work",
                        title="执行",
                        intent="执行安全步骤",
                        success_criteria_refs=[],
                        expected_outcome=ExpectedObservation(kind="result", success_condition="done"),
                    )
                ]
            ),
        )
        events = await repo.list_events(run.id)
        cursor = events[-1].id
        node = plan.nodes[0]
        await PlanRepository(session).transition_node(node.id, PlanNodeStatus.running)
        await PlanRepository(session).transition_node(
            node.id,
            PlanNodeStatus.failed,
            failure={
                "category": "tool_error",
                "code": "FAILED",
                "message": "secret=abc /Users/private/host.txt",
                "credential": "abc",
            },
        )
        await repo.update_run_status(run.id, "failed", summary="失败")
        await session.commit()
        run_id = run.id

    response = await app_client.get(f"/api/runs/{run_id}/events?after_id={cursor}")
    streamed = [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ")]
    transitions = [item for item in streamed if item.get("type") == "plan.node.updated"]

    assert [item["payload"]["status"] for item in transitions] == ["running", "failed"]
    assert transitions[1]["payload"]["previous_status"] == "running"
    assert transitions[1]["payload"]["failure"] == {
        "category": "tool_error",
        "code": "FAILED",
    }
    assert "secret=abc" not in response.text
    assert "/Users/private" not in response.text


async def test_run_task_is_retained_until_background_execution_finishes(app_client, monkeypatch):
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_runner(run_id, settings):
        started.set()
        await release.wait()

    monkeypatch.setattr(app_client._astra_run_dispatcher, "_run_starter", delayed_runner)
    created = await app_client.post("/api/runs", json={"goal": "后台任务引用测试"})
    run_id = created.json()["run_id"]
    await started.wait()

    assert run_id in app_client._astra_run_dispatcher.active_run_ids()
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert run_id not in app_client._astra_run_dispatcher.active_run_ids()


async def test_active_run_can_be_cancelled_idempotently(app_client, monkeypatch):
    started = asyncio.Event()

    async def delayed_runner(run_id, settings):
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(app_client._astra_run_dispatcher, "_run_starter", delayed_runner)
    created = await app_client.post("/api/runs", json={"goal": "持续生成回答"})
    run_id = created.json()["run_id"]
    await started.wait()

    cancelled = await app_client.post(f"/api/runs/{run_id}/cancel")
    cancelled_again = await app_client.post(f"/api/runs/{run_id}/cancel")

    assert cancelled.status_code == cancelled_again.status_code == 200
    assert cancelled.json()["status"] == cancelled_again.json()["status"] == "cancelled"
    assert cancelled.json()["terminal_reason"]["category"] == "user_cancelled"
    assert [event["type"] for event in cancelled_again.json()["events"]].count("run.cancelled") == 1
    assert run_id not in app_client._astra_run_dispatcher.active_run_ids()


async def test_cancel_run_survives_background_cleanup_failure(app_client, monkeypatch):
    started = asyncio.Event()

    async def cleanup_failing_runner(run_id, settings):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError as exc:
            raise RuntimeError("simulated cleanup failure") from exc

    monkeypatch.setattr(
        app_client._astra_run_dispatcher,
        "_run_starter",
        cleanup_failing_runner,
    )
    created = await app_client.post("/api/runs", json={"goal": "生成流式回答"})
    run_id = created.json()["run_id"]
    await started.wait()

    cancelled = await app_client.post(f"/api/runs/{run_id}/cancel")

    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert cancelled.json()["terminal_reason"]["category"] == "user_cancelled"
    assert run_id not in app_client._astra_run_dispatcher.active_run_ids()


async def test_cancel_run_returns_completed_snapshot_and_missing_run_is_404(app_client):
    created = await app_client.post("/api/runs", json={"goal": "已完成任务"})
    run_id = created.json()["run_id"]
    async with app_client._astra_session() as session:
        from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

        repository = RunUnitOfWork(session)
        await repository.update_run_status(run_id, "completed", summary="自然完成", result={"summary": "自然完成"})
        await repository.commit()

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
    assert body["execution_profile"]["version"] == 2
    assert body["execution_profile"]["plan_execution"] == "confirm"
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
                "reflection_enabled": True,
                "execution_mode": "request_approval",
            },
        },
    )
    body = (await app_client.get(f"/api/runs/{created.json()['run_id']}")).json()
    assert created.json()["answer_mode"] == "standard"
    assert body["answer_mode"] == "standard"
    assert body["runtime_kind"] == "fast-v1"
    assert body["runtime_version"] == 1
    assert body["execution_profile"]["assurance_level"] == "basic"
    assert body["execution_profile"]["version"] == 2
    assert body["execution_profile"]["plan_execution"] is None
    assert body["reasoning_policy"]["effective"]["reasoning_effort"] == "fast"
    assert "planning_strategy" not in body["reasoning_policy"]["effective"]
    assert body["reasoning_policy"]["effective"]["reflection_enabled"] is False
    assert body["reasoning_policy"]["effective"]["budgets"]["max_tool_calls"] is None
    assert body["reasoning_policy"]["effective"]["budgets"]["max_turns"] is None


async def test_required_subagent_run_is_routed_to_trusted_runtime(app_client):
    created = await app_client.post(
        "/api/runs",
        json={
            "goal": "快速并发比较两个方案",
            "answer_mode": "standard",
            "subagent_mode": "required",
        },
    )

    assert created.status_code == 200
    body = (await app_client.get(f"/api/runs/{created.json()['run_id']}")).json()
    assert body["answer_mode"] == "trusted"
    assert body["runtime_kind"] == "trusted-v1"
    assert body["execution_profile"]["subagent_mode"] == "required"
    assert body["chat_messages"][0]["content"] == "/subagent 快速并发比较两个方案"
    assert body["chat_messages"][0]["metadata"]["command"] == "/subagent"
    assert body["execution_profile"]["plan_execution"] == "auto"
    assert body["reasoning_policy"]["effective"]["subagents"]["enabled"] is True
    assert body["reasoning_policy"]["effective"]["subagents"]["budgets"]["max_children_total"] > 0
    assert body["task_contract"] == {}
    assert body["plan_graph"] == {}
    assert body["state_version"] == 0


async def test_required_subagent_run_fails_closed_when_swarm_is_disabled(app_client):
    updated = await app_client.put("/api/tools/swarm/state", json={"enabled": False})
    assert updated.status_code == 200

    response = await app_client.post(
        "/api/runs",
        json={
            "goal": "并发调研两个独立方案",
            "answer_mode": "trusted",
            "plan_execution": "auto",
            "subagent_mode": "required",
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SUBAGENT_COMMAND_UNAVAILABLE"


async def test_tool_approval_decision_api_consumes_token_once(app_client):
    async with app_client._astra_session() as session:
        repo = RunUnitOfWork(session)
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
            ApprovalRequestCreate(
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
        )
        waiting = await repo.set_waiting_state(
            run.id,
            {"kind": "tool_approval", "approval_id": approval.id, "tool_call_id": call.id},
        )
        token = waiting.waiting_state["continuation_token"]
        run_id = run.id
        await repo.commit()

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
        loaded = await RunUnitOfWork(session).require_run(run_id)
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
    from app.application.run_management.lifecycle.settings import RunSettingsResolver

    configured = RunSettingsResolver.apply_model_config(
        AstraRuntimeSettings(model_provider="mock"),
        {
            "provider": provider,
            "name": "test-model",
            "api_key": "secret",
            "base_url": "https://example.test/v1",
        },
    )

    assert configured.model_provider == provider


@pytest.mark.parametrize("provider", ["ollama", "lmstudio", "vllm", "localai", "compatible", "mock"])
def test_model_config_allows_keyless_local_providers(provider):
    from app.application.run_management.lifecycle.settings import RunSettingsResolver

    configured = RunSettingsResolver.apply_model_config(
        AstraRuntimeSettings(model_provider="mock"),
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
    response = await app_client.post(f"/api/runs/{created.json()['run_id']}/resume", json={"content": "继续"})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "RUN_NOT_WAITING"


async def test_resume_rejects_switching_the_frozen_run_model(app_client, monkeypatch):
    created = await app_client.post("/api/runs", json={"goal": "需要补充信息"})
    run_id = created.json()["run_id"]
    async with app_client._astra_session() as session:
        from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

        await RunUnitOfWork(session).set_waiting_state(
            run_id,
            {"request": "请补充", "continuation_token": "resume-token"},
        )

    scheduled = {}

    async def record_start(scheduled_run_id, settings):
        scheduled.update(run_id=scheduled_run_id, settings=settings)

    monkeypatch.setattr(app_client._astra_run_dispatcher, "_run_starter", record_start)
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

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "RUN_MODEL_MISMATCH"
    assert scheduled == {}


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


async def test_model_thinking_capabilities_api_is_batched_and_secret_free(app_client):
    response = await app_client.post(
        "/api/models/thinking-capabilities/resolve",
        json={
            "models": [
                {"provider": "openai", "model": "gpt-5.2"},
                {"provider": "qwen", "model": "qwen3.7-plus"},
                {"provider": "google", "model": "gemini-2.5-pro"},
            ]
        },
    )

    assert response.status_code == 200
    capabilities = response.json()["capabilities"]
    assert [item["toggle"] for item in capabilities] == [
        "optional",
        "optional",
        "unavailable",
    ]
    assert all(item["capability_version"] == 2 for item in capabilities)
    assert all("api_key" not in item and "base_url" not in item for item in capabilities)


async def test_create_run_persists_explicit_model_thinking_snapshot(app_client):
    response = await app_client.post(
        "/api/runs",
        json={
            "goal": "独立配置模型思考",
            "reasoning_policy": {"reasoning_effort": "fast"},
            "model": {
                "provider": "qwen",
                "name": "qwen3.7-plus",
                "api_key": "secret",
                "base_url": "https://example.test/v1",
                "thinking": {
                    "enabled": True,
                    "depth": "high",
                    "capability_version": 1,
                },
            },
        },
    )

    assert response.status_code == 200
    run = (await app_client.get(f"/api/runs/{response.json()['run_id']}")).json()
    assert run["reasoning_policy"]["effective"]["reasoning_effort"] == "fast"
    assert "base_url" not in run["model_policy"]
    assert run["model_policy"]["thinking"] == {
        "requested": {
            "enabled": True,
            "depth": "high",
            "capability_version": 1,
        },
        "effective": {"enabled": True, "depth": "high"},
        "source": "explicit_model_control",
        "adapter": "qwen-hybrid-thinking",
        "adjustments": [
            {
                "field": "capability_version",
                "requested": 1,
                "effective": 2,
                "reason": "capability_version_changed",
            }
        ],
        "capability_version": 2,
    }


@pytest.mark.parametrize(
    (
        "answer_mode",
        "provider",
        "model",
        "thinking",
        "expected_source",
        "expected_enabled",
        "expected_depth",
    ),
    [
        (
            "standard",
            "qwen",
            "qwen3.7-plus",
            {"enabled": False, "capability_version": 1},
            "explicit_model_control",
            False,
            None,
        ),
        (
            "trusted",
            "openai",
            "gpt-5",
            {"enabled": False, "capability_version": 1},
            "explicit_model_control",
            True,
            "medium",
        ),
        (
            "standard",
            "openai",
            "gpt-4o",
            {
                "enabled": True,
                "depth": "high",
                "capability_version": 1,
            },
            "explicit_model_control",
            False,
            None,
        ),
        (
            "trusted",
            "qwen",
            "qwen3.7-plus",
            None,
            "model_default",
            True,
            "medium",
        ),
    ],
)
async def test_model_thinking_api_smoke_matrix_across_answer_modes(
    app_client,
    answer_mode,
    provider,
    model,
    thinking,
    expected_source,
    expected_enabled,
    expected_depth,
):
    model_config = {
        "provider": provider,
        "name": model,
        "api_key": "secret",
        "base_url": "https://example.test/v1",
    }
    if thinking is not None:
        model_config["thinking"] = thinking
    response = await app_client.post(
        "/api/runs",
        json={
            "goal": "模型思考端到端烟雾验证",
            "answer_mode": answer_mode,
            "model": model_config,
        },
    )

    assert response.status_code == 200
    run = (await app_client.get(f"/api/runs/{response.json()['run_id']}")).json()
    snapshot = run["model_policy"]["thinking"]
    assert snapshot["source"] == expected_source
    assert snapshot["effective"] == {
        "enabled": expected_enabled,
        "depth": expected_depth,
    }


async def test_create_run_rejects_provider_native_thinking_fields(app_client):
    response = await app_client.post(
        "/api/runs",
        json={
            "goal": "拒绝原生字段",
            "model": {
                "provider": "qwen",
                "name": "qwen3.7-plus",
                "api_key": "secret",
                "base_url": "https://example.test/v1",
                "thinking_budget": 8192,
            },
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_INVALID"
