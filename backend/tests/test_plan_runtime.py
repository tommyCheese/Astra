import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import PlanNodeRecord
from app.repositories.plans import PlanRepository, PlanStateError, plan_to_view
from app.repositories.runs import RunRepository, run_to_view
from app.runner.planning import (
    PlanScheduler,
    PlanService,
    PlanValidationError,
    PlanValidator,
    canonical_agent_state,
)
from app.runner.reasoning import build_default_contract
from app.schemas.agent import (
    ExpectedObservation,
    PlanDraft,
    PlanNodeDraft,
    PlanNodeStatus,
    PlanPatch,
    PlanPatchOperation,
    PlanningStrategy,
)


def weather_plan() -> PlanDraft:
    return PlanDraft(
        strategy=PlanningStrategy.plan_first,
        nodes=[
            PlanNodeDraft(
                node_key="resolve-location",
                title="解析地点",
                intent="确定天气查询地点",
                success_criteria_refs=["criterion-result"],
                expected_outcome=ExpectedObservation(
                    kind="query_parameters",
                    success_condition="location is resolved",
                    required_fields=["location"],
                ),
            ),
            PlanNodeDraft(
                node_key="fetch-weather",
                title="查询天气",
                intent="获取天气预报",
                depends_on=["resolve-location"],
                required_capabilities=["weather.lookup"],
                success_criteria_refs=["criterion-result"],
                expected_outcome=ExpectedObservation(
                    kind="weather_result",
                    success_condition="forecast is available",
                    required_fields=["temperature", "condition"],
                ),
            ),
            PlanNodeDraft(
                node_key="answer",
                title="生成建议",
                intent="给出天气与出行建议",
                depends_on=["fetch-weather"],
                success_criteria_refs=["criterion-result"],
                expected_outcome=ExpectedObservation(
                    kind="final_answer", success_condition="answer is supported"
                ),
            ),
        ],
    )


def test_validator_accepts_weather_plan_and_rejects_cycles():
    contract = build_default_contract("查询天气")
    validator = PlanValidator()
    assert validator.validate(
        weather_plan(),
        task_contract=contract,
        available_capabilities={"weather.lookup"},
    )
    cyclic = weather_plan().model_copy(deep=True)
    cyclic.nodes[0].depends_on = ["answer"]
    with pytest.raises(PlanValidationError, match="cycle"):
        validator.validate(
            cyclic,
            task_contract=contract,
            available_capabilities={"weather.lookup"},
        )


def test_validator_rejects_unknown_references_and_capabilities():
    contract = build_default_contract("查询天气")
    draft = weather_plan().model_copy(deep=True)
    draft.nodes[1].success_criteria_refs = ["missing"]
    with pytest.raises(PlanValidationError, match="Unknown success criteria"):
        PlanValidator().validate(draft, task_contract=contract)
    draft.nodes[1].success_criteria_refs = ["criterion-result"]
    with pytest.raises(PlanValidationError, match="Unavailable capabilities"):
        PlanValidator().validate(
            draft, task_contract=contract, available_capabilities={"web_search"}
        )


async def test_plan_repository_persists_graph_and_projects_run_view(session):
    run = await RunRepository(session).create_task_run("查询天气", {"provider": "mock"})
    contract = build_default_contract("查询天气")
    repository = PlanRepository(session)
    plan = await PlanService(repository).create(
        run.id,
        weather_plan(),
        contract=contract,
        capabilities={"weather.lookup"},
    )
    state = canonical_agent_state(contract, plan, policy_version=1)
    await RunRepository(session).initialize_reasoning_state(
        run.id,
        task_contract=contract.model_dump(mode="json"),
        plan_graph=plan_to_view(plan).model_dump(mode="json"),
        agent_state=state.model_dump(mode="json"),
    )

    loaded = await RunRepository(session).require_run(run.id)
    view = run_to_view(loaded)
    assert view["plan_graph"]["id"] == plan.id
    assert [item["node_key"] for item in view["steps"]] == [
        "resolve-location",
        "fetch-weather",
        "answer",
    ]
    assert view["steps"][1]["depends_on"] == ["resolve-location"]
    assert loaded.agent_state["active_plan_id"] == plan.id
    assert loaded.agent_state.get("plan") is None


async def test_plan_version_and_node_key_constraints(session):
    run = await RunRepository(session).create_task_run("约束测试", {"provider": "mock"})
    repository = PlanRepository(session)
    plan = await repository.create(run.id, weather_plan())
    session.add(
        PlanNodeRecord(
            plan_id=plan.id,
            node_key="resolve-location",
            index=99,
            title="duplicate",
            intent="duplicate",
        )
    )
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()


async def test_scheduler_blocks_dependencies_and_releases_successors(session):
    run = await RunRepository(session).create_task_run("查询天气", {"provider": "mock"})
    repository = PlanRepository(session)
    plan = await repository.create(run.id, weather_plan())
    scheduler = PlanScheduler(repository)
    assert [item.node_key for item in scheduler.ready_nodes(plan)] == ["resolve-location"]

    first = await scheduler.select_next(run.id)
    assert first and first.node_key == "resolve-location"
    await repository.transition_node(first.id, PlanNodeStatus.completed, evidence_refs=["obs-1"])
    refreshed = await repository.require(plan.id)
    assert [item.node_key for item in scheduler.ready_nodes(refreshed)] == ["fetch-weather"]


async def test_scheduler_selects_branch_nodes_deterministically(session):
    run = await RunRepository(session).create_task_run("并行候选", {"provider": "mock"})
    contract = build_default_contract("并行候选")
    draft = PlanDraft(
        strategy=PlanningStrategy.adaptive,
        nodes=[
            PlanNodeDraft(
                node_key="a",
                title="A",
                intent="A",
                success_criteria_refs=["criterion-result"],
                expected_outcome=ExpectedObservation(kind="step_result", success_condition="A"),
            ),
            PlanNodeDraft(
                node_key="b",
                title="B",
                intent="B",
                success_criteria_refs=["criterion-result"],
                expected_outcome=ExpectedObservation(kind="step_result", success_condition="B"),
            ),
        ],
    )
    repository = PlanRepository(session)
    plan = await PlanService(repository).create(run.id, draft, contract=contract)
    assert [node.node_key for node in PlanScheduler.ready_nodes(plan)] == ["a", "b"]
    selected = await PlanScheduler(repository).select_next(run.id)
    assert selected and selected.node_key == "a"


async def test_illegal_node_transition_is_rejected(session):
    run = await RunRepository(session).create_task_run("状态转换", {"provider": "mock"})
    repository = PlanRepository(session)
    plan = await repository.create(run.id, weather_plan())
    with pytest.raises(PlanStateError, match="pending -> completed"):
        await repository.transition_node(plan.nodes[0].id, PlanNodeStatus.completed)


async def test_plan_patch_creates_version_and_preserves_lineage(session):
    run = await RunRepository(session).create_task_run("重规划", {"provider": "mock"})
    contract = build_default_contract("重规划")
    repository = PlanRepository(session)
    service = PlanService(repository)
    plan = await service.create(
        run.id,
        weather_plan(),
        contract=contract,
        capabilities={"weather.lookup"},
    )
    patch = PlanPatch(
        expected_plan_version=plan.version,
        reason="增加空气质量检查",
        operations=[
            PlanPatchOperation(
                operation="add_node",
                node=PlanNodeDraft(
                    node_key="air-quality",
                    title="检查空气质量",
                    intent="补充空气质量",
                    depends_on=["fetch-weather"],
                    success_criteria_refs=["criterion-result"],
                    expected_outcome=ExpectedObservation(
                        kind="air_quality", success_condition="AQI available"
                    ),
                    optional=True,
                ),
            ),
            PlanPatchOperation(
                operation="add_dependency",
                node_key="answer",
                predecessor_key="air-quality",
            ),
        ],
    )
    revised = await service.apply_patch(
        run.id,
        patch,
        contract=contract,
        capabilities={"weather.lookup"},
    )
    assert revised.version == 2
    assert revised.supersedes_plan_id == plan.id
    assert {node.node_key for node in revised.nodes} == {
        "resolve-location",
        "fetch-weather",
        "air-quality",
        "answer",
    }
    assert next(node for node in revised.nodes if node.node_key == "answer").lineage_node_id


async def test_stale_plan_patch_is_rejected(session):
    run = await RunRepository(session).create_task_run("过期补丁", {"provider": "mock"})
    contract = build_default_contract("过期补丁")
    repository = PlanRepository(session)
    service = PlanService(repository)
    await service.create(run.id, weather_plan(), contract=contract, capabilities={"weather.lookup"})
    with pytest.raises(PlanStateError, match="version conflict"):
        await service.apply_patch(
            run.id,
            PlanPatch(
                expected_plan_version=99,
                reason="stale",
                operations=[
                    PlanPatchOperation(
                        operation="update_node",
                        node_key="answer",
                        updates={"title": "changed"},
                    )
                ],
            ),
            contract=contract,
            capabilities={"weather.lookup"},
        )
