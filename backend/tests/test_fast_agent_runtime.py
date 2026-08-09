import ast
from pathlib import Path

import pytest

from app.application.agent_runtime.policies.reasoning import resolve_run_profile
from app.application.run_management.execution.run_execution import RunExecution as RunEngine
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.types import AnswerMode
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.runtime.standard import (
    _canonical_model_action,
    standard_compatible_skills,
)
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolRegistry,
    AstraToolSpec,
    ToolExecutionError,
    ToolResultEnvelope,
)


class ScriptedFastClient(MockModelClient):
    def __init__(self, actions):
        self.actions = list(actions)
        self.contexts = []

    async def standard_decide(self, goal, context, *, on_delta=None):
        self.contexts.append(context)
        action = self.actions.pop(0)
        if action["action"] == "answer" and on_delta:
            await on_delta(action["content"])
            await on_delta("\1")
        return action


class ReadTool(AstraTool):
    spec = AstraToolSpec(
        name="read_value",
        version="1",
        input_schema={"type": "object", "required": ["key"]},
        output_schema={"type": "object", "required": ["value"]},
        permission="network_read",
        side_effect_level="read_only",
    )

    async def run(self, tool_input, *, context=None):
        return ToolResultEnvelope(data={"value": f"value:{tool_input['key']}"}).model_dump(mode="json")


class WriteTool(AstraTool):
    spec = AstraToolSpec(
        name="write_value",
        version="1",
        input_schema={"type": "object", "required": ["value"]},
        output_schema={"type": "object"},
        permission="workspace_write",
        side_effect_level="write",
        risk="high",
    )

    async def run(self, tool_input, *, context=None):
        raise AssertionError("approval-required tool must not execute before approval")


class FailingTool(AstraTool):
    spec = AstraToolSpec(
        name="sandbox_failure",
        version="1",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permission="process_execute",
        side_effect_level="read_only",
    )

    async def run(self, tool_input, *, context=None):
        raise ToolExecutionError("sandbox_execution_failed", "password=must-not-be-persisted")


async def create_fast_run(repo, settings, goal="fast goal", execution_mode="auto_approval"):
    profile = resolve_run_profile(
        AnswerMode.standard,
        RequestedReasoningPolicy(execution_mode=execution_mode),
    )
    return await repo.create_task_run(
        goal,
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="standard",
        execution_profile=profile.model_dump(mode="json"),
    )


async def test_fast_runtime_direct_answer_has_no_trusted_state(session, tmp_path):
    settings = AstraRuntimeSettings(
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
    )
    repo = RunUnitOfWork(session)
    run = await create_fast_run(repo, settings)
    client = ScriptedFastClient([{"protocol_version": 1, "action": "answer", "content": "fast answer"}])

    await RunEngine(settings, model_client=client, tool_registry=AstraToolRegistry())._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert loaded.runtime_kind == "fast-v1"
    assert loaded.status == "completed"
    assert loaded.task_contract == {}
    assert loaded.plan_graph == {}
    assert loaded.agent_state == {}
    assert loaded.steps == []
    assert loaded.turns == []
    assert loaded.memories == []
    assert loaded.artifacts == []
    assert loaded.result["verification_report"] is None
    assert loaded.result["completion_decision"] is None
    assert client.contexts[0]["allowed_actions"] == ["answer", "call_tool", "ask_user", "stop"]
    assert "task_contract" not in client.contexts[0]
    assert "plan_graph" not in client.contexts[0]


async def test_fast_runtime_observes_tool_result_then_answers(session, tmp_path):
    settings = AstraRuntimeSettings(
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
    )
    repo = RunUnitOfWork(session)
    run = await create_fast_run(repo, settings, "read a value")
    client = ScriptedFastClient(
        [
            {"protocol_version": 1, "action": "call_tool", "tool_name": "read_value", "tool_input": {"key": "a"}},
            {"protocol_version": 1, "action": "answer", "content": "value:a"},
        ]
    )
    registry = AstraToolRegistry().extend([ReadTool()])

    await RunEngine(settings, model_client=client, tool_registry=registry)._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert loaded.status == "completed"
    assert len(loaded.tool_calls) == 1
    assert loaded.fast_state_version >= 2
    assert client.contexts[1]["recent_observations"][0]["kind"] == "tool_result"


async def test_fast_runtime_pauses_at_shared_approval_boundary(session, tmp_path):
    settings = AstraRuntimeSettings(
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
    )
    repo = RunUnitOfWork(session)
    run = await create_fast_run(repo, settings, "write a value", "request_approval")
    client = ScriptedFastClient(
        [{"protocol_version": 1, "action": "call_tool", "tool_name": "write_value", "tool_input": {"value": "x"}}]
    )

    await RunEngine(
        settings,
        model_client=client,
        tool_registry=AstraToolRegistry().extend([WriteTool()]),
    )._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert loaded.status == "waiting_user"
    assert loaded.waiting_state["kind"] == "tool_approval"
    assert loaded.fast_runtime_snapshot["recent_observations"][-1]["data"]["category"] == "approval_required"
    assert len(loaded.approval_requests) == 1
    assert loaded.tool_calls[0].status == "awaiting_approval"


async def test_fast_runtime_multi_tool_failure_recovery_and_terminal_convergence(session, tmp_path):
    settings = AstraRuntimeSettings(
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
    )
    repo = RunUnitOfWork(session)
    run = await create_fast_run(repo, settings, "recover from tools")
    client = ScriptedFastClient(
        [
            {
                "protocol_version": 1,
                "action": "call_tool",
                "tool_name": "read_value",
                "tool_input": {"key": "first"},
            },
            {
                "protocol_version": 1,
                "action": "call_tool",
                "tool_name": "sandbox_failure",
                "tool_input": {},
            },
            {
                "protocol_version": 1,
                "action": "call_tool",
                "tool_name": "read_value",
                "tool_input": {"key": "second"},
            },
            {"protocol_version": 1, "action": "answer", "content": "recovered"},
        ]
    )
    registry = AstraToolRegistry().extend([ReadTool(), FailingTool()])

    await RunEngine(settings, model_client=client, tool_registry=registry)._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert loaded.status == "completed"
    assert [call.status for call in loaded.tool_calls] == ["succeeded", "failed", "succeeded"]
    error_observation = client.contexts[2]["recent_observations"][-1]
    assert error_observation["data"]["category"] == "sandbox_execution_failed"
    assert "must-not-be-persisted" not in str(loaded.fast_runtime_snapshot)


async def test_fast_runtime_asks_user_and_cleans_forged_artifact_reference(session, tmp_path):
    settings = AstraRuntimeSettings(
        model_provider="mock",
        artifact_store_path=str(tmp_path / "artifacts"),
        task_workspace_store_path=str(tmp_path / "workspaces"),
    )
    repo = RunUnitOfWork(session)
    question = await create_fast_run(repo, settings, "ambiguous")
    await RunEngine(
        settings,
        model_client=ScriptedFastClient([{"protocol_version": 1, "action": "ask_user", "content": "Which one?"}]),
        tool_registry=AstraToolRegistry(),
    )._run_with_repo(repo, question.id)
    loaded_question = await repo.require_run(question.id)
    assert loaded_question.status == "waiting_user"
    assert loaded_question.waiting_state["kind"] == "fast_user_question"

    forged = await create_fast_run(repo, settings, "forge")
    await RunEngine(
        settings,
        model_client=ScriptedFastClient(
            [
                {
                    "protocol_version": 1,
                    "action": "answer",
                    "content": "open artifact://deadbeef-dead-beef-dead-beefdeadbeef",
                }
            ]
        ),
        tool_registry=AstraToolRegistry(),
    )._run_with_repo(repo, forged.id)
    loaded_forged = await repo.require_run(forged.id)
    assert "artifact:" not in loaded_forged.summary
    assert loaded_forged.result["audit_refs"]["referenced_artifact_ids"] == []


def test_fast_runtime_package_has_no_trusted_lifecycle_imports():
    root = Path(__file__).parents[1] / "app" / "application" / "fast_agent_runtime"
    forbidden = ("planning", "reflection", "completion_gate", "verification")
    imports = []
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
            elif isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
    assert not [name for name in imports if any(part in name for part in forbidden)]


def test_fast_skill_compatibility_excludes_trusted_capabilities():
    skills = [
        {"qualified_identity": "custom:plain", "metadata": {}},
        {"qualified_identity": "custom:planned", "metadata": {"recommended_answer_mode": "trusted"}},
        {"qualified_identity": "custom:memory", "metadata": {"required_capabilities": ["memory_write"]}},
    ]

    assert [item["qualified_identity"] for item in standard_compatible_skills(skills)] == ["custom:plain"]


def test_fast_model_action_cannot_add_runtime_authority():
    with pytest.raises(ValueError):
        _canonical_model_action(
            {
                "protocol_version": 1,
                "action": "call_tool",
                "tool_name": "write_value",
                "tool_input": {"value": "x"},
                "allowed_tools": ["write_value"],
                "skip_approval": True,
            }
        )
