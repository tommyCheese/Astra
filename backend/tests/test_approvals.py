import pytest

from app.core.config import Settings
from app.repositories.runs import RunRepository, run_to_view
from app.runner.agent_loop import AgentLoop
from app.runner.approvals import matcher_matches, similar_matcher
from app.runner.model_client import MockModelClient
from app.runner.reasoning import PolicyCompiler
from app.schemas.agent import AgentDecision, RequestedReasoningPolicy
from app.tools.base import Tool, ToolExecutionError, ToolRegistry, ToolSpec


class FakeWrite(Tool):
    spec = ToolSpec(
        name="file_write",
        version="test",
        input_schema={"required": ["path", "content"]},
        output_schema={},
        permission="workspace_write",
        permissions=["workspace_write", "process_execute_unknown"],
        side_effect_level="persistent_side_effect",
        idempotent=False,
    )

    async def run(self, tool_input, *, context=None):
        self.last_context = context
        return {"path": tool_input["path"], "written": True}


class WriteModelClient(MockModelClient):
    async def decide(self, goal, context):
        if not any(
            item.get("kind") == "tool_result"
            and item.get("data", {}).get("tool_name") == "file_write"
            for item in context.get("observations", [])
        ):
            return AgentDecision(
                decision_type="call_tool",
                reasoning_summary="写入任务文件。",
                tool_name="file_write",
                tool_input={"path": "report.txt", "content": goal},
                expected_observation="文件写入完成。",
                stop_condition="写入完成后结束。",
            )
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="文件已写入。",
            expected_observation="返回完成结果。",
        )


class AlwaysWriteModelClient(MockModelClient):
    async def decide(self, goal, context):
        return AgentDecision(
            decision_type="call_tool",
            reasoning_summary="重复写入任务文件。",
            tool_name="file_write",
            tool_input={"path": "report.txt", "content": goal},
        )


def fake_write_registry():
    return ToolRegistry().extend([FakeWrite()])


def policy(mode: str) -> dict:
    return PolicyCompiler().compile(
        RequestedReasoningPolicy(execution_mode=mode, reflection_enabled=False)
    ).model_dump(mode="json")


async def test_request_approval_freezes_tool_before_execution(session):
    settings = Settings(model_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "查询批准流程", settings.model_policy, reasoning_policy=policy("request_approval")
    )
    registry = fake_write_registry()

    result = await AgentLoop(
        settings, model_client=WriteModelClient(), tool_registry=registry
    ).run(repo, run.id, run.task.description)
    loaded = await repo.require_run(run.id)
    view = run_to_view(loaded)

    assert result["status"] == "waiting_user"
    assert loaded.tool_calls[0].status == "awaiting_approval"
    assert not hasattr(registry.get("file_write"), "last_context")
    assert view["pending_approval"]["tool_name"] == "file_write"
    assert view["pending_approval"]["decisions"] == [
        "approve_once", "allow_similar", "allow_task", "reject"
    ]


async def test_approve_once_resumes_exact_frozen_call(session):
    settings = Settings(model_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "查询批准恢复", settings.model_policy, reasoning_policy=policy("request_approval")
    )
    registry = fake_write_registry()
    client = WriteModelClient()
    loop = AgentLoop(settings, model_client=client, tool_registry=registry)
    await loop.run(repo, run.id, run.task.description)
    waiting = await repo.require_run(run.id)
    approval = waiting.approval_requests[-1]

    await repo.decide_approval(
        run.id,
        approval.id,
        "approve_once",
        continuation_token=waiting.waiting_state["continuation_token"],
    )
    await loop.run(repo, run.id, run.task.description)
    loaded = await repo.require_run(run.id)

    assert loaded.tool_calls[0].status == "succeeded"
    assert loaded.tool_calls[0].input == approval.frozen_input
    assert registry.get("file_write").last_context.tool_call_id == loaded.tool_calls[0].id
    assert loaded.status in {"executing", "completed"}


async def test_approval_resume_preserves_tool_budget_and_does_not_execute_twice(session):
    settings = Settings(model_provider="mock", agent_max_tool_calls=1)
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "只写入一次 report.txt",
        settings.model_policy,
        reasoning_policy=PolicyCompiler().compile(
            RequestedReasoningPolicy(
                reasoning_effort="fast",
                execution_mode="request_approval",
                max_tool_calls=1,
                reflection_enabled=False,
            )
        ).model_dump(mode="json"),
    )
    registry = fake_write_registry()
    loop = AgentLoop(
        settings,
        model_client=AlwaysWriteModelClient(),
        tool_registry=registry,
    )
    await loop.run(repo, run.id, run.task.description)
    waiting = await repo.require_run(run.id)
    approval = waiting.approval_requests[-1]
    await repo.decide_approval(
        run.id,
        approval.id,
        "approve_once",
        continuation_token=waiting.waiting_state["continuation_token"],
    )

    result = await loop.run(repo, run.id, run.task.description)
    loaded = await repo.require_run(run.id)

    assert result["status"] == "blocked"
    assert len(loaded.tool_calls) == 1
    assert loaded.tool_calls[0].status == "succeeded"


async def test_rejection_never_executes_rejected_call_and_replay_fails(session):
    settings = Settings(model_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "拒绝工具", settings.model_policy, reasoning_policy=policy("request_approval")
    )
    registry = fake_write_registry()
    await AgentLoop(settings, model_client=WriteModelClient(), tool_registry=registry).run(
        repo, run.id, run.task.description
    )
    waiting = await repo.require_run(run.id)
    approval = waiting.approval_requests[-1]
    token = waiting.waiting_state["continuation_token"]

    await repo.decide_approval(run.id, approval.id, "reject", continuation_token=token)
    with pytest.raises(ValueError):
        await repo.decide_approval(run.id, approval.id, "reject", continuation_token=token)
    loaded = await repo.require_run(run.id)

    assert loaded.tool_calls[0].status == "rejected"
    assert not hasattr(registry.get("file_write"), "last_context")
    assert loaded.agent_state["observations"][-1]["kind"] == "approval_result"


async def test_approved_action_fails_closed_when_frozen_input_is_tampered(session):
    settings = Settings(model_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "验证冻结输入", settings.model_policy, reasoning_policy=policy("request_approval")
    )
    registry = fake_write_registry()
    loop = AgentLoop(settings, model_client=WriteModelClient(), tool_registry=registry)
    await loop.run(repo, run.id, run.task.description)
    waiting = await repo.require_run(run.id)
    approval = waiting.approval_requests[-1]
    await repo.decide_approval(
        run.id,
        approval.id,
        "approve_once",
        continuation_token=waiting.waiting_state["continuation_token"],
    )
    call = waiting.tool_calls[0]
    call.input = {"query": "tampered"}
    await session.commit()

    with pytest.raises(ToolExecutionError) as error:
        await loop.run(repo, run.id, run.task.description)

    assert error.value.category == "approval_integrity_error"
    assert not hasattr(registry.get("file_write"), "last_context")


def test_bash_similar_matchers_are_narrow_and_complex_commands_are_exact_only():
    matcher = similar_matcher("bash_execute", {"command": "pytest tests/test_api.py -q"})

    assert matcher == {"kind": "command_prefix", "tokens": ["pytest"]}
    assert matcher_matches(matcher, {"command": "pytest tests/test_tools.py -q"})
    assert not matcher_matches(matcher, {"command": "python -m pytest"})
    assert similar_matcher("bash_execute", {"command": "pytest && rm -rf /tmp/x"}) is None
