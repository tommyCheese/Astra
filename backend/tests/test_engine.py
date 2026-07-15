import json

import pytest
from fake_web_tools import fake_web_registry

from app.agent_profile import AgentProfileLoader, load_agent_profile
from app.core.config import Settings
from app.repositories.plans import PlanRepository, plan_to_view
from app.repositories.runs import RunRepository
from app.runner.engine import RunEngine
from app.runner.model_client import MockModelClient
from app.runner.planning import PlanScheduler, PlanService, canonical_agent_state
from app.runner.reasoning import PolicyCompiler, RunProfileResolver, build_default_contract
from app.schemas.agent import (
    AgentDecision,
    AnswerMode,
    ExpectedObservation,
    FinalAnswer,
    PlanDraft,
    PlanningStrategy,
    PlanNodeDraft,
    PlanOutput,
    PlanStep,
    RequestedReasoningPolicy,
)
from app.tools.base import Tool, ToolRegistry, ToolSpec


class FakeWeather(Tool):
    spec = ToolSpec(
        name="weather_lookup",
        version="test",
        input_schema={"required": ["location", "date"]},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
    )

    async def run(self, tool_input, *, context=None):
        return {
            "location": tool_input["location"],
            "date": tool_input["date"],
            "temperature": {"min": 27, "max": 34},
            "condition": "阵雨",
            "precipitation_probability": 70,
        }


class WeatherPlanClient(MockModelClient):
    async def plan(self, goal):
        return PlanOutput(
            steps=[
                PlanStep(title="解析查询条件", intent="确定上海和明天"),
                PlanStep(
                    title="查询天气",
                    intent="调用 weather_lookup",
                    required_tools=["weather_lookup"],
                ),
                PlanStep(title="评估跑步条件", intent="分析温度和降雨"),
                PlanStep(title="生成回答", intent="给出天气与跑步建议"),
            ],
            required_tools=["weather_lookup"],
            success_criteria=["天气与建议完整"],
        )

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        active = context.get("active_node")
        if active is None:
            return AgentDecision(
                decision_type="finalize", reasoning_summary="计划已完成"
            ), FinalAnswer(
                summary="明天上海有阵雨且最高约 34°C，不建议长距离户外跑步。",
                findings=[{"text": "降雨概率较高，建议改为室内训练。"}],
            )
        if active["node_key"] == "step-2" and not any(
            item.get("data", {}).get("tool_name") == "weather_lookup"
            for item in context.get("observations", [])
        ):
            return AgentDecision(
                decision_type="call_tool",
                reasoning_summary="查询明天上海天气",
                tool_name="weather_lookup",
                tool_input={"location": "上海", "date": "tomorrow"},
                target_step_id=active["id"],
            ), None
        return AgentDecision(
            decision_type="complete_node",
            reasoning_summary=f"完成 {active['title']}",
            target_step_id=active["id"],
        ), None


class RecoveryClient(MockModelClient):
    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        active = context.get("active_node")
        if active is not None:
            return AgentDecision(
                decision_type="complete_node",
                reasoning_summary="确认恢复结果并完成节点",
                target_step_id=active["id"],
            ), None
        return await super().decide_with_answer(
            goal,
            context,
            on_delta=on_delta,
            on_reasoning_delta=on_reasoning_delta,
        )


class FinalizeActiveNodeClient(MockModelClient):
    def __init__(self):
        self.answer_callbacks: list[bool] = []

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        self.answer_callbacks.append(on_delta is not None)
        active = context.get("active_node")
        if active is not None:
            return AgentDecision(
                decision_type="finalize",
                reasoning_summary="完成当前计划节点",
            ), FinalAnswer(summary="尚未提交的节点内答案")
        assert on_delta is not None
        await on_delta("真正的")
        await on_delta("流式回答")
        await on_delta("\1")
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="计划完成后生成正式回答",
        ), FinalAnswer(summary="真正的流式回答")


async def test_engine_completes_mock_web_query(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run("查询 mock 数据", settings.model_policy)

    engine = RunEngine(
        settings,
        model_client=MockModelClient(),
        tool_registry=fake_web_registry(),
    )
    await engine._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert loaded.status == "completed"
    assert loaded.result["sources"]
    assert loaded.result["source_quality"]
    assert loaded.result["verification_notes"]
    assert loaded.steps == []
    canonical_plan = await PlanRepository(session).active_for_run(run.id)
    assert canonical_plan is not None
    assert canonical_plan.status == "completed"
    assert all(node.status == "completed" for node in canonical_plan.nodes)
    assert all(call.plan_node_id == canonical_plan.nodes[0].id for call in loaded.tool_calls)

    evidence_artifacts = [
        artifact for artifact in loaded.artifacts if artifact.type == "evidence_pack"
    ]
    assert evidence_artifacts
    evidence_pack = json.loads(evidence_artifacts[0].content_ref)
    assert evidence_pack["fetched_sources"]
    succeeded_fetch_calls = [
        call
        for call in loaded.tool_calls
        if call.tool_name == "web_fetch" and call.status == "succeeded"
    ]
    assert len(evidence_pack["fetched_sources"]) == len(succeeded_fetch_calls)

    events = await repo.list_events(run.id)
    event_types = [event.type for event in events]
    planning_index = event_types.index("reasoning.phase.started")
    selecting_index = next(
        index
        for index, event in enumerate(events)
        if event.type == "reasoning.phase.started"
        and event.payload.get("phase") == "selecting_action"
    )
    summary_index = event_types.index("reasoning.summary.completed")
    turn_index = event_types.index("agent_turn.created")
    tool_index = event_types.index("tool_call.started")
    assert planning_index < selecting_index < summary_index < turn_index < tool_index
    process_events = [event for event in events if event.type.startswith("reasoning.")]
    assert all("reasoning_content" not in event.payload for event in process_events)
    assert all("tool_input" not in event.payload for event in process_events)


async def test_weather_plan_executes_nodes_in_dependency_order(session):
    settings = Settings(model_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "查询明天上海天气并判断是否适合跑步",
        settings.model_policy,
        reasoning_policy=engine_policy("plan_first", "request_approval"),
    )
    registry = ToolRegistry()
    registry.register(FakeWeather())
    await RunEngine(
        settings, model_client=WeatherPlanClient(), tool_registry=registry
    )._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    plan = await PlanRepository(session).active_for_run(run.id)
    assert loaded.status == "completed"
    assert plan is not None
    assert [node.status for node in plan.nodes] == ["completed"] * 4
    call = next(item for item in loaded.tool_calls if item.tool_name == "weather_lookup")
    assert call.plan_node_id == plan.nodes[1].id
    events = await repo.list_events(run.id)
    selected = [event.payload["node_key"] for event in events if event.type == "plan.node.selected"]
    assert selected == ["step-1", "step-2", "step-3", "step-4"]


async def test_answer_delta_batching_flushes_first_and_final_content(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run("流式批处理", settings.model_policy)
    engine = RunEngine(settings, model_client=MockModelClient(), tool_registry=fake_web_registry())

    await engine._start_answer_stream(repo, run.id)
    await engine._handle_answer_delta(repo, run.id, "首")
    await engine._handle_answer_delta(repo, run.id, "尾")
    await engine._handle_answer_delta(repo, run.id, "\1")
    await engine._complete_answer_stream(repo, run.id, "首尾")

    events = await repo.list_events(run.id)
    assert [event.type for event in events] == [
        "run.created",
        "answer.started",
        "answer.delta",
        "answer.delta",
        "answer.settling",
        "answer.completed",
    ]
    assert events[-1].payload == {"content": "首尾", "status": "answer_complete"}


async def test_final_plan_node_answer_is_regenerated_as_canonical_stream(session):
    settings = Settings(model_provider="mock")
    profile = RunProfileResolver().resolve(AnswerMode.standard, RequestedReasoningPolicy())
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "生成一个流式回答",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
    )
    client = FinalizeActiveNodeClient()

    await RunEngine(settings, model_client=client, tool_registry=ToolRegistry())._run_with_repo(
        repo, run.id
    )

    loaded = await repo.require_run(run.id)
    events = await repo.list_events(run.id)
    deltas = [event.payload["delta"] for event in events if event.type == "answer.delta"]
    assert loaded.result["summary"] == "真正的流式回答"
    assert client.answer_callbacks == [False, True]
    assert deltas == ["真正的", "流式回答"]
    assert events.index(next(event for event in events if event.type == "answer.delta")) < events.index(
        next(event for event in events if event.type == "answer.completed")
    )


async def test_engine_resumes_with_frozen_profile_when_packaged_default_changes(
    session, monkeypatch
):
    repo = RunRepository(session)
    frozen = load_agent_profile()
    run = await repo.create_task_run(
        "恢复 Profile", {"provider": "mock"}, agent_profile_snapshot=frozen.snapshot()
    )
    changed_contents = {document.name: document.content for document in frozen.manifest.documents}
    changed_contents["soul"] = changed_contents["soul"].replace("Astra 真诚", "Astra 始终真诚", 1)
    changed = AgentProfileLoader().load(changed_contents)
    monkeypatch.setattr("app.runner.engine.load_agent_profile", lambda: changed)

    selected = await RunEngine(Settings(model_provider="mock"))._profile_for_run(
        repo, run.id, run.agent_profile_snapshot
    )

    assert selected.manifest.version == frozen.manifest.version
    assert selected.manifest.version != changed.manifest.version


class PlanningSpyClient(MockModelClient):
    def __init__(self):
        self.contract_calls = 0
        self.plan_calls = 0

    async def contract(self, goal):
        self.contract_calls += 1
        return await super().contract(goal)

    async def plan(self, goal):
        self.plan_calls += 1
        return await super().plan(goal)


class EffortSpyClient(MockModelClient):
    def __init__(self):
        self.bound_efforts = []

    def bind_reasoning_effort(self, effort):
        self.bound_efforts.append(str(effort))


def engine_policy(
    planning_strategy, execution_mode="request_approval", reasoning_effort="balanced"
):
    return (
        PolicyCompiler()
        .compile(
            RequestedReasoningPolicy(
                planning_strategy=planning_strategy,
                execution_mode=execution_mode,
                reasoning_effort=reasoning_effort,
            )
        )
        .model_dump(mode="json")
    )


async def test_engine_binds_effective_reasoning_effort_before_model_operations(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "查询 mock 数据",
        settings.model_policy,
        reasoning_policy=engine_policy("direct", reasoning_effort="deep"),
    )
    client = EffortSpyClient()

    await RunEngine(
        settings, model_client=client, tool_registry=fake_web_registry()
    )._run_with_repo(repo, run.id)

    assert client.bound_efforts == ["deep"]


async def test_standard_profile_skips_model_contract_and_uses_basic_assurance(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    profile = RunProfileResolver().resolve(
        AnswerMode.standard, RequestedReasoningPolicy(reasoning_effort="deep")
    )
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "快速回答",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
    )
    client = PlanningSpyClient()

    await RunEngine(
        settings, model_client=client, tool_registry=fake_web_registry()
    )._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert client.contract_calls == 0
    assert loaded.answer_mode == "standard"
    assert loaded.result["answer_mode"] == "standard"
    assert loaded.result["assurance_level"] == "basic"
    assert loaded.result["verification_report"]["assurance_level"] == "basic"
    assert loaded.result["completion_decision"]["reason"] == "快速回答已完成基础保障检查。"


@pytest.mark.parametrize(
    ("strategy", "mode", "contract_calls", "plan_calls"),
    [
        ("direct", "request_approval", 0, 0),
        ("adaptive", "request_approval", 1, 0),
        ("plan_first", "request_approval", 1, 1),
        ("direct", "plan_only", 1, 1),
    ],
)
async def test_engine_planning_strategy_selects_distinct_path(
    session, strategy, mode, contract_calls, plan_calls
):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "查询 mock 数据", settings.model_policy, reasoning_policy=engine_policy(strategy, mode)
    )
    client = PlanningSpyClient()

    await RunEngine(
        settings, model_client=client, tool_registry=fake_web_registry()
    )._run_with_repo(repo, run.id)

    assert client.contract_calls == contract_calls
    assert client.plan_calls == plan_calls


async def test_plan_only_result_does_not_expose_internal_conversation_wrapper(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    previous = await repo.create_task_run("第一轮问题", settings.model_policy)
    await repo.update_run_status(previous.id, "completed", summary="第一轮回答")
    current = await repo.create_task_run(
        "第二轮规划",
        settings.model_policy,
        previous.task_id,
        reasoning_policy=engine_policy("plan_first", "plan_only"),
    )

    await RunEngine(
        settings, model_client=MockModelClient(), tool_registry=fake_web_registry()
    )._run_with_repo(repo, current.id)

    loaded = await repo.require_run(current.id)
    result_text = "\n".join(item["text"] for item in loaded.result["findings"])
    assert "Conversation context" not in result_text
    assert "Current user request" not in result_text
    assert "第二轮规划" in result_text
    planned = await PlanRepository(session).latest_planned_for_run(current.id)
    assert planned is not None
    assert planned.status == "planned"
    assert all(node.status == "pending" for node in planned.nodes)
    assert loaded.agent_state["active_plan_id"] is None


async def test_plan_only_plan_can_be_activated_and_executed(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run(
        "先规划再执行",
        settings.model_policy,
        reasoning_policy=engine_policy("plan_first", "plan_only"),
    )
    registry = ToolRegistry()
    registry.register(FakeWeather())
    engine = RunEngine(settings, model_client=WeatherPlanClient(), tool_registry=registry)
    await engine._run_with_repo(repo, run.id)
    planned = await PlanRepository(session).latest_planned_for_run(run.id)
    assert planned is not None
    await PlanRepository(session).activate(planned.id, expected_version=planned.version)
    loaded = await repo.require_run(run.id)
    state = dict(loaded.agent_state)
    state.update(
        {
            "active_plan_id": planned.id,
            "active_plan_version": planned.version,
            "active_node_id": None,
            "version": loaded.state_version + 1,
        }
    )
    await repo.update_reasoning_state(
        run.id,
        expected_version=loaded.state_version,
        agent_state=state,
        plan_graph=plan_to_view(planned).model_dump(mode="json"),
    )
    await engine._run_with_repo(repo, run.id)
    executed = await PlanRepository(session).active_for_run(run.id)
    assert executed is not None
    assert all(node.status == "completed" for node in executed.nodes)


async def test_engine_replays_recorded_checkpoint_without_duplicate_tool_call(session):
    settings = Settings(model_provider="mock", web_search_provider="mock")
    repo = RunRepository(session)
    run = await repo.create_task_run("恢复已经记录的搜索结果", settings.model_policy)
    contract = build_default_contract(run.task.description)
    plan = await PlanService(PlanRepository(session)).create(
        run.id,
        PlanDraft(
            strategy=PlanningStrategy.direct,
            nodes=[
                PlanNodeDraft(
                    node_key="step-1",
                    title="查询资料",
                    intent="查询并恢复结果",
                    required_capabilities=["web_search"],
                    success_criteria_refs=[criterion.id for criterion in contract.success_criteria],
                    expected_outcome=ExpectedObservation(
                        kind="tool_result",
                        success_condition="搜索结果已记录",
                    ),
                )
            ],
        ),
        contract=contract,
        capabilities={"web_search", "network_read"},
    )
    state = canonical_agent_state(contract, plan, policy_version=1)
    await repo.initialize_reasoning_state(
        run.id,
        task_contract=contract.model_dump(mode="json"),
        plan_graph=plan_to_view(plan).model_dump(mode="json"),
        agent_state=state.model_dump(mode="json"),
    )
    node = await PlanScheduler(PlanRepository(session)).select_next(run.id)
    assert node is not None
    turn = await repo.create_agent_turn(
        run.id,
        1,
        "call_tool",
        "执行搜索",
        selected_tool="web_search",
        plan_node_id=node.id,
        state_version_before=run.state_version,
        phase="executing",
        idempotency_key="stable-search-key",
    )
    call = await repo.start_tool_call(
        run.id,
        None,
        "web_search",
        "test",
        {"query": "checkpoint"},
        "network_read",
        "read_only",
        plan_node_id=node.id,
    )
    await repo.finish_tool_call(
        call.id,
        output={
            "query": "checkpoint",
            "candidates": [
                {
                    "title": "Recovered source",
                    "url": "https://example.test/recovered",
                    "snippet": "recorded result",
                }
            ],
        },
    )
    await repo.update_agent_turn(
        turn.id,
        tool_call_id=call.id,
        phase="result_recorded",
    )

    await RunEngine(
        settings,
        model_client=RecoveryClient(),
        tool_registry=fake_web_registry(),
    )._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert len(loaded.tool_calls) == 1
    assert any(
        event.type == "reasoning.checkpoint_recovered"
        and event.payload.get("action") == "replay_result"
        for event in loaded.events
    )
