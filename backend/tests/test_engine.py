from collections import Counter
from unittest.mock import AsyncMock

import pytest
from fake_information_tools import fake_information_registry
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.agent_runtime.policies.reasoning import (
    build_default_contract,
    resolve_run_profile,
)
from app.application.planning.scheduler import PlanScheduler
from app.application.planning.service import PlanService, canonical_agent_state
from app.application.run_management.execution import service as engine_module
from app.application.run_management.execution.service import RunExecution as RunEngine
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.planning import ExpectedObservation, PlanDraft, PlanNodeDraft
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.common.schemas.agent.types import AnswerMode, PlanExecution
from app.domain.agent_profile import load_agent_profile
from app.infrastructure.db.model_base import AstraOrmRecordBase
from app.infrastructure.db.models.permissions import AgentIdentityRecord, ToolCatalogSnapshotRecord
from app.infrastructure.db.models.workspaces import TaskWorkspaceRecord, WorkspaceCheckpointRecord
from app.infrastructure.model_clients.contracts import ModelOutputError
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.repositories.plans import PlanRepository, plan_to_view
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolRegistry,
    AstraToolSpec,
    ToolResultEnvelope,
)


class FakeWeather(AstraTool):
    spec = AstraToolSpec(
        name="weather_lookup",
        version="test",
        input_schema={"required": ["location", "date"]},
        output_schema={},
        permission="network_read",
        side_effect_level="read_only",
        task_capabilities=["weather.lookup"],
    )

    async def run(self, tool_input, *, context=None):
        return ToolResultEnvelope(
            data={
                "location": tool_input["location"],
                "date": tool_input["date"],
                "temperature": {"min": 27, "max": 34},
                "condition": "阵雨",
                "precipitation_probability": 70,
            }
        ).model_dump(mode="json")


async def test_cancelled_answer_flush_reuses_active_repository_session():
    engine = RunEngine(AstraRuntimeSettings(model_provider="mock"), model_client=MockModelClient())
    repo = AsyncMock()
    repo.session = AsyncMock()
    engine.answers._answer_buffers["run-1"] = "停止前的部分回答"
    engine.answers._answer_start_pending.add("run-1")

    await engine._flush_cancelled_answer(repo, "run-1")

    assert repo.add_event.await_args_list[0].args == (
        "run-1",
        "answer.started",
        {"role": "assistant", "mode": "native"},
    )
    assert repo.add_event.await_args_list[0].kwargs == {"flush": False}
    assert repo.add_event.await_args_list[1].args == (
        "run-1",
        "answer.delta",
        {"delta": "停止前的部分回答"},
    )
    repo.session.commit.assert_awaited_once()
    assert "run-1" not in engine.answers._answer_buffers


async def test_engine_rolls_back_failed_stage_before_persisting_terminal_error(monkeypatch):
    session = AsyncMock()
    repository = AsyncMock()
    repository.session = session

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, traceback):
            return None

    monkeypatch.setattr(engine_module, "SessionLocal", lambda: SessionContext())
    monkeypatch.setattr(engine_module, "RunUnitOfWork", lambda _session: repository)

    engine = RunEngine(AstraRuntimeSettings(model_provider="mock"), model_client=MockModelClient())
    engine._run_with_repo = AsyncMock(side_effect=RuntimeError("stage failed"))

    await engine.run("run-1")

    session.rollback.assert_awaited_once()
    repository.add_event.assert_awaited_once()
    assert repository.add_event.await_args.args[:2] == ("run-1", "run.error")
    repository.update_run_status.assert_awaited_once()
    assert repository.update_run_status.await_args.args[:2] == ("run-1", "failed")
    session.commit.assert_awaited_once()


async def test_engine_run_commits_terminal_status_for_a_new_session(monkeypatch, tmp_path):
    database_path = tmp_path / "engine-terminal-status.db"
    database_engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        future=True,
    )
    session_factory = async_sessionmaker(database_engine, expire_on_commit=False)
    async with database_engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)

    settings = AstraRuntimeSettings(
        model_provider="mock",
        task_workspace_store_path=str(tmp_path / "workspaces"),
        artifact_store_path=str(tmp_path / "artifacts"),
    )
    profile = resolve_run_profile(
        AnswerMode.standard,
        RequestedReasoningPolicy(),
    )
    async with session_factory() as setup_session:
        repository = RunUnitOfWork(setup_session)
        run = await repository.create_task_run(
            "你好",
            settings.model_policy,
            reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
            answer_mode="standard",
            execution_profile=profile.model_dump(mode="json"),
        )
        await setup_session.commit()
        run_id = run.id

    monkeypatch.setattr(engine_module, "SessionLocal", session_factory)
    await RunEngine(settings, model_client=MockModelClient()).run(run_id)

    async with session_factory() as verification_session:
        completed = await RunUnitOfWork(verification_session).require_run(run_id)
        assert completed.status == "completed"
        assert completed.summary == "已围绕目标完成 Web 数据查询：你好"

    await database_engine.dispose()


class WeatherPlanClient(MockModelClient):
    async def plan(self, goal, *, contract):
        criterion_ids = [item.id for item in contract.success_criteria]
        return PlanDraft(
            nodes=[
                PlanNodeDraft(
                    node_key="step-1",
                    title="解析查询条件",
                    intent="确定上海和明天",
                    success_criteria_refs=criterion_ids,
                    expected_outcome=ExpectedObservation(
                        kind="query_parameters",
                        success_condition="地点和日期已确定",
                    ),
                ),
                PlanNodeDraft(
                    node_key="step-2",
                    title="查询天气",
                    intent="获取指定地点和日期的天气信息",
                    depends_on=["step-1"],
                    required_capabilities=["weather.lookup"],
                    success_criteria_refs=criterion_ids,
                    expected_outcome=ExpectedObservation(
                        kind="weather_result",
                        success_condition="天气结果可用",
                    ),
                ),
                PlanNodeDraft(
                    node_key="step-3",
                    title="评估跑步条件",
                    intent="分析温度和降雨",
                    depends_on=["step-2"],
                    success_criteria_refs=criterion_ids,
                    expected_outcome=ExpectedObservation(
                        kind="analysis",
                        success_condition="跑步条件已评估",
                    ),
                ),
                PlanNodeDraft(
                    node_key="step-4",
                    title="生成回答",
                    intent="给出天气与跑步建议",
                    depends_on=["step-3"],
                    success_criteria_refs=criterion_ids,
                    expected_outcome=ExpectedObservation(
                        kind="final_answer",
                        success_condition="建议已生成",
                    ),
                ),
            ],
        )

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        active = context.get("active_node")
        if active is None:
            return AgentDecision(decision_type="finalize", reasoning_summary="计划已完成"), AgentFinalAnswer(
                summary="明天上海有阵雨且最高约 34°C，不建议长距离户外跑步。",
                findings=[{"text": "降雨概率较高，建议改为室内训练。"}],
            )
        if active["node_key"] == "step-2" and not any(
            item.get("data", {}).get("tool_name") == "weather_lookup" for item in context.get("observations", [])
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
            ), AgentFinalAnswer(summary="尚未提交的节点内答案")
        assert on_delta is not None
        await on_delta("真正的")
        await on_delta("流式回答")
        await on_delta("\1")
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="计划完成后生成正式回答",
        ), AgentFinalAnswer(summary="真正的流式回答")


class QuickStreamingClient(MockModelClient):
    def __init__(self):
        self.decide_calls = 0

    async def contract(self, goal):
        raise AssertionError("standard fast path must not build a task contract")

    async def plan(self, goal, **kwargs):
        raise AssertionError("standard fast path must not build a plan")

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        self.decide_calls += 1
        assert context["answer_mode"] == "standard"
        assert context["runtime"] == "fast-v1"
        assert "task_contract" not in context
        assert "plan_graph" not in context
        assert "memory_reads" not in context
        assert on_delta is not None
        assert on_reasoning_delta is not None
        await on_reasoning_delta("正在")
        await on_reasoning_delta("直接回答")
        await on_reasoning_delta("\1")
        await on_delta("立即")
        await on_delta("流式回答")
        await on_delta("\1")
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="直接回答",
        ), AgentFinalAnswer(summary="立即流式回答")


class QuickClarificationClient(MockModelClient):
    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        return AgentDecision(
            decision_type="ask_user",
            reasoning_summary="输入缺少可执行目标，应请求用户澄清。",
        ), None


class StreamThenModelErrorClient(MockModelClient):
    def __init__(self):
        self.final_answer_calls = 0

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        active = context.get("active_node")
        if active is not None:
            return AgentDecision(
                decision_type="complete_node",
                reasoning_summary=f"完成 {active['title']}",
                target_step_id=active["id"],
            ), None
        self.final_answer_calls += 1
        assert on_delta is not None
        await on_delta("保留已经展示的")
        await on_delta("完整回答")
        await on_delta("\1")
        raise ModelOutputError("invalid auxiliary answer structure")


class StreamWithoutStructuredAnswerClient(StreamThenModelErrorClient):
    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        active = context.get("active_node")
        if active is not None:
            return AgentDecision(
                decision_type="complete_node",
                reasoning_summary=f"完成 {active['title']}",
                target_step_id=active["id"],
            ), None
        self.final_answer_calls += 1
        assert on_delta is not None
        await on_delta("采用已经展示的完整回答")
        await on_delta("\1")
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="完成最终回答",
        ), None


class QuickPermissionTimingClient(QuickStreamingClient):
    def __init__(self, after_first_delta):
        super().__init__()
        self.after_first_delta = after_first_delta

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        self.decide_calls += 1
        assert on_delta is not None
        assert on_reasoning_delta is not None
        await on_reasoning_delta("正在直接回答")
        await on_reasoning_delta("\1")
        await on_delta("立即")
        await self.after_first_delta()
        await on_delta("流式回答")
        await on_delta("\1")
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="直接回答",
        ), AgentFinalAnswer(summary="立即流式回答")


class QuickToolClient(QuickStreamingClient):
    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        self.decide_calls += 1
        assert on_reasoning_delta is not None
        manifest = context["tool_manifests"]["weather_lookup"]
        assert manifest["input_schema"] == {"required": ["location", "date"]}
        assert manifest["permission"] == "network_read"
        assert "output_schema" not in manifest
        assert "version" not in manifest
        if not context["observations"]:
            await on_reasoning_delta("查询天气")
            await on_reasoning_delta("\1")
            return AgentDecision(
                decision_type="call_tool",
                reasoning_summary="查询天气",
                tool_name="weather_lookup",
                tool_input={"location": "上海", "date": "tomorrow"},
            ), None
        assert on_delta is not None
        await on_reasoning_delta("返回工具结果")
        await on_reasoning_delta("\1")
        await on_delta("适合室内训练")
        await on_delta("\1")
        return AgentDecision(
            decision_type="finalize",
            reasoning_summary="返回工具结果",
        ), AgentFinalAnswer(summary="适合室内训练")


class QuickForbiddenToolClient(QuickStreamingClient):
    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        self.decide_calls += 1
        assert on_reasoning_delta is not None
        if len(context["observations"]) >= 2:
            await on_reasoning_delta("确认禁止工具不可用")
            await on_reasoning_delta("\1")
            return AgentDecision(
                decision_type="finalize",
                reasoning_summary="确认禁止工具不可用",
            ), AgentFinalAnswer(summary="禁止工具未被执行")
        await on_reasoning_delta("尝试不存在的工具")
        await on_reasoning_delta("\1")
        return AgentDecision(
            decision_type="call_tool",
            reasoning_summary="尝试不存在的工具",
            tool_name="unregistered_tool",
            tool_input={},
        ), None


async def test_engine_completes_mock_web_query(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(execution_mode="auto_approval"),
        plan_execution=PlanExecution.auto,
    )
    run = await repo.create_task_run(
        "查询 mock 数据",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )

    engine = RunEngine(
        settings,
        model_client=MockModelClient(),
        tool_registry=fake_information_registry(),
    )
    await engine._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert loaded.status == "completed"
    assert loaded.result["verification_notes"]
    assert loaded.steps == []
    canonical_plan = await PlanRepository(session).active_for_run(run.id)
    assert canonical_plan is not None
    assert canonical_plan.status == "completed"
    execution_node = next(node for node in canonical_plan.nodes if node.required_capabilities)
    assert execution_node.required_capabilities == [
        "information.search",
        "information.read",
    ]
    assert all(call.plan_node_id == execution_node.id for call in loaded.tool_calls)

    events = await repo.list_events(run.id)
    event_types = [event.type for event in events]
    planning_index = event_types.index("reasoning.phase.started")
    selecting_index = next(
        index
        for index, event in enumerate(events)
        if event.type == "reasoning.phase.started" and event.payload.get("phase") == "selecting_action"
    )
    summary_index = event_types.index("reasoning.summary.completed")
    turn_index = event_types.index("agent_turn.created")
    tool_index = event_types.index("tool_call.started")
    assert planning_index < selecting_index < summary_index < turn_index < tool_index
    process_events = [event for event in events if event.type.startswith("reasoning.")]
    assert all("reasoning_content" not in event.payload for event in process_events)
    assert all("tool_input" not in event.payload for event in process_events)
    resolutions = [
        event.payload
        for event in events
        if event.type == "tool.resolution.candidates" and event.payload.get("plan_node_id") == execution_node.id
    ]
    assert any(payload["unresolved_capabilities"] == ["information.read", "information.search"] for payload in resolutions)
    assert any(payload["unresolved_capabilities"] == ["information.read"] for payload in resolutions)
    assert {event.payload["tool_name"] for event in events if event.type == "tool.selection.accepted"} >= {
        "catalog_search",
        "catalog_read",
    }


async def test_trusted_skill_checks_become_provenanced_completion_criteria():
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(execution_mode="auto_approval"),
        plan_execution=PlanExecution.auto,
    )
    engine = RunEngine(settings, model_client=MockModelClient())
    engine._active_skill_blocks = [
        {
            "qualified_identity": "custom:verified-workflow",
            "revision_id": "revision-1",
            "digest": "sha256:abc",
            "instructions": "Verify the result.",
            "metadata": {"mandatory_checks": ["Confirm the generated artifact exists."]},
        }
    ]
    contract, plan = await engine._prepare_plan(
        "run-skill-check",
        "create an artifact",
        profile.reasoning_policy.model_dump(mode="json"),
        profile.model_dump(mode="json"),
    )
    criterion = next(item for item in contract.success_criteria if item.id.startswith("skill-check-"))
    assert criterion.mandatory is True
    assert criterion.verification_method == "task_adapter"
    assert criterion.provenance["qualified_identity"] == "custom:verified-workflow"
    assert contract.skill_revisions[0]["digest"] == "sha256:abc"
    assert all("custom:verified-workflow" in node.required_skill_ids for node in plan.nodes)


async def test_weather_plan_executes_nodes_in_dependency_order(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(execution_mode="auto_approval"),
        plan_execution=PlanExecution.auto,
    )
    run = await repo.create_task_run(
        "查询明天上海天气并判断是否适合跑步",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )
    registry = AstraToolRegistry()
    registry.register(FakeWeather())
    await RunEngine(settings, model_client=WeatherPlanClient(), tool_registry=registry)._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    plan = await PlanRepository(session).active_for_run(run.id)
    assert loaded.status == "completed"
    assert plan is not None
    assert plan.status == "completed"
    assert [node.status for node in plan.nodes] == ["completed"] * 4
    call = next(item for item in loaded.tool_calls if item.tool_name == "weather_lookup")
    assert call.plan_node_id == plan.nodes[1].id


async def test_trusted_confirmation_activates_exact_plan_once_before_execution(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(execution_mode="auto_approval"),
        plan_execution=PlanExecution.confirm,
    )
    run = await repo.create_task_run(
        "查询明天上海天气并判断是否适合跑步",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )
    registry = AstraToolRegistry()
    registry.register(FakeWeather())
    engine = RunEngine(settings, model_client=WeatherPlanClient(), tool_registry=registry)

    await engine._run_with_repo(repo, run.id)
    waiting = await repo.require_run(run.id)
    binding = dict(waiting.waiting_state or {})
    assert waiting.status == "waiting_user"
    assert binding["kind"] == "plan_confirmation"
    assert binding["plan_id"] == waiting.plan_graph["id"]
    assert binding["plan_version"] == waiting.plan_graph["version"]
    assert binding["state_version"] == waiting.state_version
    assert waiting.active_plan_id is None
    assert waiting.tool_calls == []

    with pytest.raises(ValueError, match="stale plan confirmation"):
        await repo.confirm_waiting_plan(
            run.id,
            continuation_token=binding["continuation_token"],
            plan_id=binding["plan_id"],
            expected_plan_version=binding["plan_version"] + 1,
            expected_state_version=binding["state_version"],
        )
    unchanged = await repo.require_run(run.id)
    assert unchanged.status == "waiting_user"
    assert unchanged.tool_calls == []

    activated = await repo.confirm_waiting_plan(
        run.id,
        continuation_token=binding["continuation_token"],
        plan_id=binding["plan_id"],
        expected_plan_version=binding["plan_version"],
        expected_state_version=binding["state_version"],
    )
    assert activated.status == "executing"
    assert activated.active_plan_id == binding["plan_id"]
    assert activated.waiting_state is None

    with pytest.raises(ValueError, match="not waiting"):
        await repo.confirm_waiting_plan(
            run.id,
            continuation_token=binding["continuation_token"],
            plan_id=binding["plan_id"],
            expected_plan_version=binding["plan_version"],
            expected_state_version=binding["state_version"],
        )

    await engine._run_with_repo(repo, run.id)
    completed = await repo.require_run(run.id)
    assert completed.status == "completed"
    assert len(completed.tool_calls) == 1
    events = await repo.list_events(run.id)
    selected = [event.payload["node_key"] for event in events if event.type == "plan.node.selected"]
    assert selected == ["step-1", "step-2", "step-3", "step-4"]


async def test_answer_delta_batching_flushes_first_and_final_content(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run("流式批处理", settings.model_policy)
    engine = RunEngine(settings, model_client=MockModelClient(), tool_registry=fake_information_registry())
    commit = AsyncMock(wraps=session.commit)
    session.commit = commit

    await engine.answers._start_answer_stream(repo, run.id)
    commit.assert_not_awaited()
    await engine.answers._handle_answer_delta(repo, run.id, "首")
    commit.assert_awaited_once()
    await engine.answers._handle_answer_delta(repo, run.id, "尾")
    await engine.answers._handle_answer_delta(repo, run.id, "\1")
    await engine.answers._complete_answer_stream(repo, run.id, "首尾")

    events = await repo.list_events(run.id)
    assert [event.type for event in events] == [
        "run.created",
        "agent_profile.frozen",
        "answer.started",
        "answer.delta",
        "answer.delta",
        "answer.content.completed",
        "answer.completed",
    ]
    assert events[-2].payload == {"background_verification": False}
    assert events[-1].payload == {"content": "首尾", "status": "answer_complete"}


@pytest.mark.parametrize("answer_mode", [AnswerMode.standard, AnswerMode.trusted])
async def test_streamed_answer_is_not_replaced_after_late_model_validation_error(session, answer_mode):
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(
        answer_mode,
        RequestedReasoningPolicy(execution_mode="auto_approval"),
        plan_execution=PlanExecution.auto if answer_mode == AnswerMode.trusted else None,
    )
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "生成一个不会被替换的回答",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
        agent_profile_snapshot=load_agent_profile().snapshot(),
    )
    client = StreamThenModelErrorClient()

    await RunEngine(settings, model_client=client, tool_registry=AstraToolRegistry())._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    events = await repo.list_events(run.id)
    event_types = [event.type for event in events]
    assert loaded.status == "completed"
    assert loaded.result["summary"] == "保留已经展示的完整回答"
    assert client.final_answer_calls == 1
    assert event_types.count("answer.started") == 1
    assert event_types.count("answer.completed") == 1
    assert event_types.count("answer.schema_degraded") == 1
    terminal_status = next(
        index
        for index, event in enumerate(events)
        if event.type == "run.status_changed" and event.payload.get("status") == "completed"
    )
    assert event_types.index("answer.completed") < terminal_status


@pytest.mark.parametrize("answer_mode", [AnswerMode.standard, AnswerMode.trusted])
async def test_streamed_answer_is_not_resynthesized_when_answer_object_is_missing(session, answer_mode):
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(
        answer_mode,
        RequestedReasoningPolicy(execution_mode="auto_approval"),
        plan_execution=PlanExecution.auto if answer_mode == AnswerMode.trusted else None,
    )
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "生成一份最终回答",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
        agent_profile_snapshot=load_agent_profile().snapshot(),
    )
    client = StreamWithoutStructuredAnswerClient()

    await RunEngine(settings, model_client=client, tool_registry=AstraToolRegistry())._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    events = await repo.list_events(run.id)
    event_types = [event.type for event in events]
    assert loaded.result["summary"] == "采用已经展示的完整回答"
    assert client.final_answer_calls == 1
    assert event_types.count("answer.started") == 1
    assert event_types.count("answer.completed") == 1
    assert event_types.count("answer.structure_adopted") == 1


async def test_final_plan_node_answer_is_regenerated_as_canonical_stream(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.auto,
    )
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "生成一个流式回答",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
    )
    client = FinalizeActiveNodeClient()

    await RunEngine(settings, model_client=client, tool_registry=AstraToolRegistry())._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    events = await repo.list_events(run.id)
    deltas = [event.payload["delta"] for event in events if event.type == "answer.delta"]
    assert loaded.result["summary"] == "真正的流式回答"
    assert client.answer_callbacks[-1] is True
    assert all(value is False for value in client.answer_callbacks[:-1])
    assert deltas == ["真正的", "流式回答"]
    event_types = [event.type for event in events]
    assert event_types.index("answer.delta") < event_types.index("answer.content.completed")
    assert event_types.index("answer.content.completed") < event_types.index("verification.created")
    assert next(event for event in events if event.type == "answer.content.completed").payload == {
        "background_verification": True
    }
    assert event_types.index("verification.created") < event_types.index("answer.completed")


async def test_standard_fast_path_skips_plan_state_and_all_quality_gates(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(AnswerMode.standard, RequestedReasoningPolicy(execution_mode="auto_approval"))
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "快速回答",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
        agent_profile_snapshot=load_agent_profile().snapshot(),
    )
    task_id = run.task_id
    session.expunge_all()

    select_statements = []

    class QueryCountingClient(QuickStreamingClient):
        selects_before_decide: int | None = None

        async def decide_with_answer(self, *args, **kwargs):
            self.selects_before_decide = len(select_statements)
            return await super().decide_with_answer(*args, **kwargs)

    client = QueryCountingClient()

    def count_selects(_conn, _cursor, statement, _parameters, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            select_statements.append(statement)

    event.listen(session.bind.sync_engine, "before_cursor_execute", count_selects)
    try:
        await RunEngine(settings, model_client=client, tool_registry=AstraToolRegistry())._run_with_repo(repo, run.id)
    finally:
        event.remove(session.bind.sync_engine, "before_cursor_execute", count_selects)

    loaded = await repo.require_run(run.id)
    events = await repo.list_events(run.id)
    assert client.decide_calls == 1
    assert loaded.status == "completed"
    assert loaded.task_contract == {}
    assert loaded.plan_graph == {}
    assert loaded.agent_state == {}
    assert loaded.steps == []
    # A newly created standard Run freezes its Tool Catalog before the model,
    # then performs startup, Skill-snapshot, pending Fast recovery, and
    # approval eligibility reads.
    assert client.selects_before_decide == 6
    # The original full-graph loading path issued 129 SELECTs here. Fast
    # finalization adds one bounded artifact-visibility read.
    assert len(select_statements) <= 13, Counter(statement.rsplit("FROM ", 1)[-1].split()[0] for statement in select_statements)
    assert await session.scalar(select(TaskWorkspaceRecord).where(TaskWorkspaceRecord.task_id == task_id)) is None
    assert await session.scalar(select(WorkspaceCheckpointRecord).where(WorkspaceCheckpointRecord.run_id == run.id)) is None
    assert await PlanRepository(session).active_for_run(run.id) is None
    assert loaded.result["summary"] == "立即流式回答"
    assert loaded.result["verification_report"] is None
    assert loaded.result["completion_decision"] is None
    assert [event.payload["delta"] for event in events if event.type == "answer.delta"] == [
        "立即",
        "流式回答",
    ]
    assert not [event for event in events if event.type.startswith("reasoning.")]
    assert [event.payload["action"] for event in events if event.type == "fast.action.decided"] == ["answer"]
    assert "verification.created" not in [event.type for event in events]
    assert "reasoning.completion_decided" not in [event.type for event in events]
    assert "reasoning.runtime_limits" not in [event.type for event in events]
    assert "reasoning.decision_validated" not in [event.type for event in events]
    assert not any(
        event.type == "reasoning.phase.started" and event.payload.get("phase") in {"planning", "selecting_action", "verifying"}
        for event in events
    )


async def test_standard_ask_user_uses_a_user_facing_fallback_question(session):
    settings = AstraRuntimeSettings(model_provider="mock", tool_states={"swarm": False})
    profile = resolve_run_profile(AnswerMode.standard, RequestedReasoningPolicy(execution_mode="auto_approval"))
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "！",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
        agent_profile_snapshot=load_agent_profile().snapshot(),
    )

    await RunEngine(
        settings,
        model_client=QuickClarificationClient(),
        tool_registry=AstraToolRegistry(),
    )._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    events = await repo.list_events(run.id)
    assert loaded.status == "waiting_user"
    assert loaded.result is None
    assert loaded.summary == "请告诉我你希望我完成的具体任务或问题。"
    assert loaded.waiting_state["request"] == loaded.summary
    assert "输入缺少可执行目标" not in loaded.waiting_state["request"]
    assert "answer.paused" in [event.type for event in events]


async def test_standard_fast_path_defers_permission_records_until_after_first_delta(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(AnswerMode.standard, RequestedReasoningPolicy(execution_mode="auto_approval"))
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "快速回答",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
    )
    observed = {}

    async def capture_permission_state():
        observed["identities"] = len(
            (await session.scalars(select(AgentIdentityRecord).where(AgentIdentityRecord.run_id == run.id))).all()
        )
        observed["catalogs"] = len(
            (await session.scalars(select(ToolCatalogSnapshotRecord).where(ToolCatalogSnapshotRecord.run_id == run.id))).all()
        )

    await RunEngine(
        settings,
        model_client=QuickPermissionTimingClient(capture_permission_state),
        tool_registry=AstraToolRegistry(),
    )._run_with_repo(repo, run.id)

    assert observed == {"identities": 0, "catalogs": 1}
    assert await session.scalar(select(AgentIdentityRecord).where(AgentIdentityRecord.run_id == run.id)) is not None
    assert await session.scalar(select(ToolCatalogSnapshotRecord).where(ToolCatalogSnapshotRecord.run_id == run.id)) is not None


async def test_standard_fast_path_reuses_tool_router_without_creating_steps(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(AnswerMode.standard, RequestedReasoningPolicy(execution_mode="auto_approval"))
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "查询天气",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
    )
    registry = AstraToolRegistry()
    registry.register(FakeWeather())
    client = QuickToolClient()

    await RunEngine(settings, model_client=client, tool_registry=registry)._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert client.decide_calls == 2
    assert loaded.status == "completed"
    assert loaded.steps == []
    assert len(loaded.tool_calls) == 1
    assert loaded.tool_calls[0].status == "succeeded"
    assert loaded.tool_calls[0].step_id is None
    assert loaded.result["summary"] == "适合室内训练"


async def test_standard_fast_path_keeps_tool_router_security_boundary(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(AnswerMode.standard, RequestedReasoningPolicy())
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "调用禁止工具",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
    )
    client = QuickForbiddenToolClient()

    await RunEngine(settings, model_client=client, tool_registry=AstraToolRegistry())._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert client.decide_calls == 3
    assert loaded.status == "completed"
    assert loaded.tool_calls == []
    assert loaded.result["verification_report"] is None
    assert loaded.result["summary"] == "禁止工具未被执行"


async def test_engine_resumes_with_current_frozen_profile(session):
    repo = RunUnitOfWork(session)
    frozen = load_agent_profile()
    run = await repo.create_task_run("恢复 Profile", {"provider": "mock"}, agent_profile_snapshot=frozen.snapshot())
    selected = await RunEngine(AstraRuntimeSettings(model_provider="mock"))._profile_for_run(
        repo, run.id, run.agent_profile_snapshot
    )

    assert selected.manifest.version == frozen.manifest.version


class PlanningSpyClient(MockModelClient):
    def __init__(self):
        self.contract_calls = 0
        self.plan_calls = 0
        self.contract_goals = []
        self.plan_goals = []

    async def contract(self, goal):
        self.contract_calls += 1
        self.contract_goals.append(goal)
        return await super().contract(goal)

    async def plan(self, goal, **kwargs):
        self.plan_calls += 1
        self.plan_goals.append(goal)
        return await super().plan(goal, **kwargs)


class EmptyPlanClient(MockModelClient):
    async def plan(self, goal, *, contract):
        return PlanDraft.model_construct(nodes=[])


class UnavailableCapabilityPlanClient(MockModelClient):
    async def plan(self, goal, *, contract):
        return PlanDraft(
            nodes=[
                PlanNodeDraft(
                    node_key="unsupported-step",
                    title="调用不可用能力",
                    intent="测试模型生成的无效能力不会终止可信运行",
                    required_capabilities=["package_install"],
                    success_criteria_refs=[item.id for item in contract.success_criteria],
                    expected_outcome=ExpectedObservation(
                        kind="step_result",
                        success_condition="任务已完成",
                    ),
                )
            ],
        )


class EffortSpyClient(MockModelClient):
    def __init__(self):
        self.bound_efforts = []
        self.bound_thinking = []

    def bind_reasoning_effort(self, effort):
        self.bound_efforts.append(str(effort))

    def bind_model_thinking(self, thinking):
        self.bound_thinking.append(thinking)


async def test_engine_binds_effective_reasoning_effort_before_model_operations(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(reasoning_effort="deep"),
        plan_execution=PlanExecution.auto,
    )
    run = await repo.create_task_run(
        "查询 mock 数据",
        {
            **settings.model_policy,
            "thinking": {
                "requested": {
                    "enabled": True,
                    "depth": "low",
                    "capability_version": 1,
                },
                "effective": {"enabled": True, "depth": "low"},
                "source": "explicit_model_control",
                "adapter": "openai-gpt5-modern",
                "adjustments": [],
                "capability_version": 1,
            },
        },
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )
    client = EffortSpyClient()

    await RunEngine(settings, model_client=client, tool_registry=fake_information_registry())._run_with_repo(repo, run.id)

    assert client.bound_efforts == ["deep"]
    assert client.bound_thinking[0].effective.model_dump() == {
        "enabled": True,
        "depth": "low",
    }


async def test_disabled_model_thinking_does_not_suppress_public_process_events(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(
        AnswerMode.standard,
        RequestedReasoningPolicy(),
    )
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "快速回答",
        {
            **settings.model_policy,
            "thinking": {
                "requested": {
                    "enabled": False,
                    "depth": None,
                    "capability_version": 1,
                },
                "effective": {"enabled": False, "depth": None},
                "source": "explicit_model_control",
                "adapter": "qwen-hybrid-thinking",
                "adjustments": [],
                "capability_version": 1,
            },
        },
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="standard",
        execution_profile=profile.model_dump(mode="json"),
    )

    await RunEngine(settings, model_client=MockModelClient(), tool_registry=fake_information_registry())._run_with_repo(
        repo, run.id
    )

    events = await repo.list_events(run.id)
    summaries = [event for event in events if event.type == "fast.action.decided"]
    assert summaries
    assert all("reasoning_content" not in event.payload for event in summaries)
    assert all(not event.type.startswith("model_thinking.") for event in events)


async def test_standard_profile_skips_planning_and_quality_assurance_objects(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    profile = resolve_run_profile(
        AnswerMode.standard,
        RequestedReasoningPolicy(reasoning_effort="deep", execution_mode="auto_approval"),
    )
    repo = RunUnitOfWork(session)
    run = await repo.create_task_run(
        "快速回答",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
    )
    client = PlanningSpyClient()

    await RunEngine(settings, model_client=client, tool_registry=fake_information_registry())._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert client.contract_calls == 0
    assert loaded.answer_mode == "standard"
    assert loaded.result["answer_mode"] == "standard"
    assert loaded.result["assurance_level"] == "basic"
    assert loaded.task_contract == {}
    assert loaded.plan_graph == {}
    assert loaded.agent_state == {}
    assert loaded.result["verification_report"] is None
    assert loaded.result["completion_decision"] is None


async def test_trusted_engine_always_builds_contract_and_complete_plan(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.auto,
    )
    run = await repo.create_task_run(
        "查询 mock 数据",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )
    client = PlanningSpyClient()

    await RunEngine(settings, model_client=client, tool_registry=fake_information_registry())._run_with_repo(repo, run.id)

    assert client.contract_calls == 1
    assert client.plan_calls == 1


async def test_follow_up_contract_excludes_private_conversation_transcript(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.confirm,
    )
    previous = await repo.create_task_run(
        "上一轮问题",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )
    previous.summary = "上一轮回答"
    previous.status = "completed"
    current = await repo.create_task_run(
        "当前问题",
        settings.model_policy,
        previous.task_id,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )
    client = PlanningSpyClient()

    await RunEngine(settings, model_client=client, tool_registry=fake_information_registry())._run_with_repo(repo, current.id)

    loaded = await repo.require_run(current.id)
    assert client.contract_goals == ["当前问题"]
    assert client.plan_goals[0].startswith("Conversation context:\n")
    assert loaded.task_contract["original_goal"] == "当前问题"
    assert "Conversation context:" not in loaded.task_contract["success_criteria"][0]["description"]


async def test_engine_falls_back_when_model_returns_empty_plan(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.confirm,
    )
    run = await repo.create_task_run(
        "解释当前报错",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )

    await RunEngine(settings, model_client=EmptyPlanClient(), tool_registry=fake_information_registry())._run_with_repo(
        repo, run.id
    )

    loaded = await repo.require_run(run.id)
    assert loaded.status == "waiting_user"
    assert loaded.plan_graph["nodes"][0]["title"] == "生成回复"
    assert loaded.agent_state["active_plan_id"] is None


async def test_trusted_engine_falls_back_when_plan_requests_unavailable_capability(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.confirm,
    )
    run = await repo.create_task_run(
        "可信计划能力校验",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )

    await RunEngine(
        settings,
        model_client=UnavailableCapabilityPlanClient(),
        tool_registry=fake_information_registry(),
    )._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert loaded.status == "waiting_user"
    assert loaded.waiting_state["kind"] == "plan_confirmation"
    assert loaded.plan_graph["nodes"][0]["title"] == "生成回复"
    assert loaded.plan_graph["nodes"][0]["required_capabilities"] == []


async def test_engine_replays_recorded_checkpoint_without_duplicate_tool_call(session):
    settings = AstraRuntimeSettings(model_provider="mock")
    repo = RunUnitOfWork(session)
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(),
        plan_execution=PlanExecution.auto,
    )
    run = await repo.create_task_run(
        "恢复已经记录的搜索结果",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode="trusted",
        execution_profile=profile.model_dump(mode="json"),
    )
    contract = build_default_contract(run.task.description)
    plan = await PlanService(PlanRepository(session)).create(
        run.id,
        PlanDraft(
            nodes=[
                PlanNodeDraft(
                    node_key="step-1",
                    title="查询资料",
                    intent="查询并恢复结果",
                    required_capabilities=["catalog_search"],
                    success_criteria_refs=[criterion.id for criterion in contract.success_criteria],
                    expected_outcome=ExpectedObservation(
                        kind="tool_result",
                        success_condition="搜索结果已记录",
                    ),
                )
            ],
        ),
        contract=contract,
        capabilities={"catalog_search", "network_read"},
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
        selected_tool="catalog_search",
        plan_node_id=node.id,
        state_version_before=run.state_version,
        phase="executing",
        idempotency_key="stable-search-key",
    )
    call = await repo.start_tool_call(
        run.id,
        None,
        "catalog_search",
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
        tool_registry=fake_information_registry(),
    )._run_with_repo(repo, run.id)

    loaded = await repo.require_run(run.id)
    assert len(loaded.tool_calls) == 1
    assert any(
        event.type == "reasoning.checkpoint_recovered" and event.payload.get("action") == "replay_result"
        for event in loaded.events
    )
