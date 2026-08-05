from fake_web_tools import fake_web_registry

from app.application.agent_runtime.policies.reasoning import AgentReasoningPolicyCompiler
from app.application.agent_runtime.services.approval import safe_preview, similar_matcher
from app.application.agent_runtime.services.loop import AstraAgentLoop
from app.application.permissions.effects import DefaultEffectAnalyzer
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.bash import BashExecuteTool


async def test_web_runtime_characterization_freezes_calls_events_observations_and_results(session):
    settings = AstraRuntimeSettings(model_provider="mock", web_search_provider="mock", agent_max_turns=8)
    policy = AgentReasoningPolicyCompiler().compile(RequestedReasoningPolicy(execution_mode="auto_approval"))
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "查询插件化前的 Web 行为",
        settings.model_policy,
        reasoning_policy=policy.model_dump(mode="json"),
    )

    output = await AstraAgentLoop(
        settings,
        model_client=MockModelClient(),
        tool_registry=fake_web_registry(),
    ).run(repo, run.id, run.task.description)
    loaded = await repo.require_run(run.id)
    events = await repo.list_events(run.id)

    assert output["status"] == "completed"
    assert [call.tool_name for call in loaded.tool_calls] == ["web_search", "web_fetch"]
    assert all(call.status == "succeeded" for call in loaded.tool_calls)
    assert loaded.tool_calls[0].output["candidate_count"] == 1
    assert loaded.tool_calls[1].output["content"] == "Deterministic test evidence"
    assert [turn.observation["data"]["tool_name"] for turn in loaded.turns[:2]] == [
        "web_search",
        "web_fetch",
    ]
    assert {event.type for event in events} >= {
        "permission.decided",
        "tool_call.completed",
        "verification.created",
    }
    assert output["result"]["sources"][0]["url"] == "https://test.invalid/source"


def test_bash_characterization_freezes_effect_approval_preview_and_matcher():
    tool_input = {"command": "touch report.txt"}

    effect = DefaultEffectAnalyzer().analyze(BashExecuteTool.spec, tool_input, task_id="task-1")

    assert effect.approval_required is True
    assert effect.summary == "创建或修改任务工作区文件"
    assert {item.kind.value for item in effect.effects} == {"workspace_write"}
    assert safe_preview("bash_execute", tool_input) == "touch report.txt"
    assert similar_matcher("bash_execute", tool_input) == {
        "kind": "command_prefix",
        "tokens": ["touch"],
    }


def test_bash_characterization_keeps_complex_command_matchers_fail_closed():
    tool_input = {"command": "pytest && rm report.txt"}

    effect = DefaultEffectAnalyzer().analyze(BashExecuteTool.spec, tool_input, task_id="task-1")

    assert effect.approval_required is True
    assert {item.kind.value for item in effect.effects} == {"process_execute_unknown"}
    assert similar_matcher("bash_execute", tool_input) is None
