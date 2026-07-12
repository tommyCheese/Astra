import pytest
from fake_web_tools import fake_web_registry

from app.core.config import Settings
from app.repositories.runs import RunRepository
from app.runner.agent_loop import AgentLoop, ToolRouter
from app.runner.model_client import MockModelClient, ModelOutputError
from app.runner.reasoning import PolicyCompiler, build_default_contract, build_plan_graph
from app.schemas.agent import (
    AcceptedFact,
    AgentDecision,
    AgentReflection,
    AgentState,
    FinalAnswer,
    PlanningStrategy,
    ReflectionPatch,
    RequestedReasoningPolicy,
)
from app.tools.base import ToolExecutionError
from app.tools.web import build_web_registry


async def test_agent_loop_completes_mock_web_run(session):
    settings = Settings(model_provider="mock", web_search_provider="mock", agent_max_turns=8)
    repo = RunRepository(session)
    run = await repo.create_task_run("查询 mock 数据", settings.model_policy)
    client = MockModelClient()
    loop = AgentLoop(settings, model_client=client, tool_registry=fake_web_registry())

    output = await loop.run(repo, run.id, run.task.description)
    loaded = await repo.require_run(run.id)

    assert output["status"] == "completed"
    assert output["result"]["sources"]
    assert output["result"]["verification_report"]["source_count"] >= 1
    assert loaded.turns
    assert any(turn.selected_tool == "web_search" for turn in loaded.turns)
    assert any(turn.selected_tool == "web_fetch" for turn in loaded.turns)
    assert any(artifact.type == "evidence_pack" for artifact in loaded.artifacts)
    assert loaded.memories


async def test_agent_loop_injects_auditable_tool_execution_context(session):
    settings = Settings(model_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run("查询上下文", settings.model_policy)
    registry = fake_web_registry()
    search = registry.get("web_search")

    await AgentLoop(settings, model_client=MockModelClient(), tool_registry=registry).run(
        repo, run.id, run.task.description
    )

    assert search.last_context.run_id == run.id
    assert search.last_context.tool_call_id
    assert search.last_context.artifact_service
    assert search.last_context.sandbox_service


async def test_agent_loop_blocks_at_turn_limit(session):
    settings = Settings(
        model_provider="mock",
        web_search_provider="mock",
        agent_max_turns=1,
        agent_max_tool_calls=1,
    )
    repo = RunRepository(session)
    run = await repo.create_task_run("查询 mock 数据", settings.model_policy)
    loop = AgentLoop(
        settings, model_client=MockModelClient(), tool_registry=build_web_registry(settings)
    )

    output = await loop.run(repo, run.id, run.task.description)

    assert output["status"] == "blocked"
    assert "没有成功抓取到可用来源" in " ".join(output["result"]["verification_report"]["notes"])


def test_tool_router_rejects_disallowed_tool():
    router = ToolRouter(build_web_registry(Settings()), allowed_tools={"web_search", "web_fetch"})

    with pytest.raises(ToolExecutionError) as exc_info:
        router.resolve("shell.run", {"cmd": "date"})

    assert exc_info.value.category == "tool_not_allowed"


def test_tool_router_rejects_unavailable_backend():
    registry = fake_web_registry()
    tool = registry.get("web_search")
    original = tool.spec
    tool.spec = original.model_copy(update={"execution_backend": "sandbox.python"})
    router = ToolRouter(registry, available_backends={"in_process"})

    with pytest.raises(ToolExecutionError) as exc_info:
        router.resolve("web_search", {"query": "Astra"})

    assert exc_info.value.category == "sandbox_unavailable"
    tool.spec = original


class ContinueDecisionClient(MockModelClient):
    def __init__(self):
        self.decide_calls = 0

    async def decide_with_answer(self, goal, context, *, on_delta=None):
        self.decide_calls += 1
        return AgentDecision(decision_type="continue", reasoning_summary="继续处理"), None


class RecoveringDecisionClient(MockModelClient):
    def __init__(self):
        self.decide_calls = 0
        self.reflect_calls = 0

    async def decide_with_answer(self, goal, context, *, on_delta=None):
        self.decide_calls += 1
        if self.decide_calls == 1:
            raise ModelOutputError("invalid decision")
        return AgentDecision(decision_type="finalize", reasoning_summary="直接完成"), FinalAnswer(
            summary="已完成"
        )

    async def reflect(self, goal, context):
        self.reflect_calls += 1
        return await super().reflect(goal, context)


class PatchingReflectionClient(RecoveringDecisionClient):
    async def reflect(self, goal, context):
        self.reflect_calls += 1
        return AgentReflection(
            trigger="model_output_failed",
            summary="记录修正后的事实并继续。",
            next_action="retry",
            retry=True,
            patch=ReflectionPatch(
                level="local",
                fact_updates=[
                    AcceptedFact(
                        id="fact-reflection",
                        statement="模型输出失败后需要重新决策。",
                        provenance={"source": "reflection"},
                    )
                ],
            ),
        )


class ToolThenFinalizeClient(MockModelClient):
    def __init__(self):
        self.decide_calls = 0
        self.reflect_calls = 0

    async def decide_with_answer(self, goal, context, *, on_delta=None):
        self.decide_calls += 1
        if self.decide_calls == 1:
            return AgentDecision(
                decision_type="continue", reasoning_summary="完成一个非终态步骤"
            ), None
        return AgentDecision(decision_type="finalize", reasoning_summary="完成"), FinalAnswer(
            summary="已完成", findings=[{"text": "完成", "source_urls": []}]
        )

    async def reflect(self, goal, context):
        self.reflect_calls += 1
        return await super().reflect(goal, context)


class RepeatedToolClient(MockModelClient):
    def __init__(self):
        self.decide_calls = 0

    async def decide_with_answer(self, goal, context, *, on_delta=None):
        self.decide_calls += 1
        return AgentDecision(
            decision_type="call_tool",
            reasoning_summary="继续搜索",
            tool_name="web_search",
            tool_input={"query": f"{goal}-{self.decide_calls}"},
        ), None


class TwoToolsThenFinalizeClient(MockModelClient):
    def __init__(self):
        self.decide_calls = 0
        self.reflect_calls = 0

    async def decide_with_answer(self, goal, context, *, on_delta=None):
        self.decide_calls += 1
        if self.decide_calls == 1:
            return AgentDecision(
                decision_type="call_tool",
                reasoning_summary="搜索",
                tool_name="web_search",
                tool_input={"query": goal},
            ), None
        if self.decide_calls == 2:
            return AgentDecision(
                decision_type="call_tool",
                reasoning_summary="抓取",
                tool_name="web_fetch",
                tool_input={"url": "https://test.invalid/source"},
            ), None
        return AgentDecision(decision_type="finalize", reasoning_summary="完成"), FinalAnswer(
            summary="已完成", findings=[{"text": "完成", "source_urls": []}]
        )

    async def reflect(self, goal, context):
        self.reflect_calls += 1
        return await super().reflect(goal, context)


def compiled_policy(**updates):
    return PolicyCompiler().compile(RequestedReasoningPolicy(**updates)).model_dump(mode="json")


@pytest.mark.parametrize(
    ("effort", "expected_turns"), [("fast", 8), ("balanced", 12), ("deep", 20)]
)
async def test_agent_loop_uses_reasoning_effort_turn_budget(session, effort, expected_turns):
    settings = Settings(model_provider="mock", agent_max_turns=20, agent_max_tool_calls=16)
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "持续处理",
        settings.model_policy,
        reasoning_policy=compiled_policy(reasoning_effort=effort, reflection_enabled=False),
    )
    client = ContinueDecisionClient()

    await AgentLoop(settings, model_client=client, tool_registry=fake_web_registry()).run(
        repo, run.id, run.task.description
    )

    assert client.decide_calls == expected_turns
    events = await repo.list_events(run.id)
    limits = next(event.payload for event in events if event.type == "reasoning.runtime_limits")
    assert limits["max_turns"] == expected_turns


async def test_fast_policy_limits_tool_calls(session):
    settings = Settings(model_provider="mock", web_search_provider="mock", agent_max_tool_calls=16)
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "重复搜索",
        settings.model_policy,
        reasoning_policy=compiled_policy(reasoning_effort="fast", reflection_enabled=False),
    )
    client = RepeatedToolClient()

    await AgentLoop(settings, model_client=client, tool_registry=fake_web_registry()).run(
        repo, run.id, run.task.description
    )
    loaded = await repo.require_run(run.id)

    assert len(loaded.tool_calls) == 5
    assert client.decide_calls == 6


async def test_deployment_hard_cap_can_lower_deep_turn_budget(session):
    settings = Settings(model_provider="mock", agent_max_turns=3)
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "受部署限制",
        settings.model_policy,
        reasoning_policy=compiled_policy(reasoning_effort="deep", reflection_enabled=False),
    )
    client = ContinueDecisionClient()

    await AgentLoop(settings, model_client=client, tool_registry=fake_web_registry()).run(
        repo, run.id, run.task.description
    )

    assert client.decide_calls == 3
    events = await repo.list_events(run.id)
    limits = next(event.payload for event in events if event.type == "reasoning.runtime_limits")
    assert limits["max_turns"] == 3


@pytest.mark.parametrize(
    ("enabled", "trigger", "expected_reflections"),
    [
        (False, "adaptive", 0),
        (True, "failure_only", 1),
        (True, "adaptive", 1),
    ],
)
async def test_model_failure_reflection_obeys_policy(
    session, enabled, trigger, expected_reflections
):
    settings = Settings(model_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "恢复错误",
        settings.model_policy,
        reasoning_policy=compiled_policy(reflection_enabled=enabled, reflection_trigger=trigger),
    )
    client = RecoveringDecisionClient()

    await AgentLoop(settings, model_client=client, tool_registry=fake_web_registry()).run(
        repo, run.id, run.task.description
    )

    assert client.reflect_calls == expected_reflections


async def test_reflection_patch_updates_persisted_agent_state(session):
    settings = Settings(model_provider="mock")
    repo = RunRepository(session)
    policy = compiled_policy(reflection_enabled=True, reflection_trigger="failure_only")
    run = await repo.create_task_run("恢复错误", settings.model_policy, reasoning_policy=policy)
    contract = build_default_contract(run.task.description)
    graph = build_plan_graph(contract, PlanningStrategy.adaptive)
    state = AgentState(task_contract=contract, plan=graph)
    await repo.initialize_reasoning_state(
        run.id,
        task_contract=contract.model_dump(mode="json"),
        plan_graph=graph.model_dump(mode="json"),
        agent_state=state.model_dump(mode="json"),
    )
    client = PatchingReflectionClient()

    await AgentLoop(settings, model_client=client, tool_registry=fake_web_registry()).run(
        repo, run.id, run.task.description
    )

    loaded = await repo.require_run(run.id)
    assert loaded.state_version == 3
    assert loaded.agent_state["accepted_facts"][0]["id"] == "fact-reflection"
    assert any(item["kind"] == "reflection" for item in loaded.agent_state["observations"])
    assert loaded.agent_state["task_contract"]["success_criteria"][0]["status"] == "satisfied"
    events = await repo.list_events(run.id)
    created = next(event for event in events if event.type == "reflection.created")
    assert created.payload["state_version"] == 2


async def test_every_turn_reflection_runs_after_successful_non_terminal_turn(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "搜索后回答",
        settings.model_policy,
        reasoning_policy=compiled_policy(reflection_trigger="every_turn"),
    )
    client = ToolThenFinalizeClient()

    await AgentLoop(settings, model_client=client, tool_registry=fake_web_registry()).run(
        repo, run.id, run.task.description
    )

    assert client.reflect_calls == 1


async def test_every_turn_reflection_stops_at_user_budget(session):
    settings = Settings(model_provider="mock", web_search_provider="mock", agent_max_reflections=6)
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "搜索抓取后回答",
        settings.model_policy,
        reasoning_policy=compiled_policy(reasoning_effort="fast", reflection_trigger="every_turn"),
    )
    client = TwoToolsThenFinalizeClient()

    await AgentLoop(settings, model_client=client, tool_registry=fake_web_registry()).run(
        repo, run.id, run.task.description
    )

    assert client.reflect_calls == 1
    events = await repo.list_events(run.id)
    skipped = [
        event
        for event in events
        if event.type == "reflection.skipped" and event.payload["signal"] == "turn_completed"
    ]
    assert skipped
