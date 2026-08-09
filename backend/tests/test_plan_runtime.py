import pytest
from sqlalchemy.exc import IntegrityError

from app.application.agent_runtime.policies.reasoning import build_default_contract
from app.application.planning.scheduler import PlanScheduler
from app.application.planning.service import (
    PlanService,
    PlanValidationError,
    PlanValidator,
    canonical_agent_state,
)
from app.common.schemas.agent.planning import (
    ExpectedObservation,
    PlanDraft,
    PlanNodeDraft,
    PlanPatch,
    PlanPatchOperation,
)
from app.common.schemas.agent.types import PlanNodeStatus
from app.infrastructure.db.models.plans import PlanNodeRecord
from app.infrastructure.repositories.plans import (
    PlanRepository,
    PlanStateError,
    diff_plans,
    plan_to_view,
)
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.run_view_projection import run_payload


def weather_plan() -> PlanDraft:
    return PlanDraft(
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
                expected_outcome=ExpectedObservation(kind="final_answer", success_condition="answer is supported"),
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
        PlanValidator().validate(draft, task_contract=contract, available_capabilities={"catalog_search"})


def test_validator_rejects_concrete_tool_bindings_and_honors_empty_catalog():
    contract = build_default_contract("查询天气")
    concrete = weather_plan().model_copy(deep=True)
    concrete.nodes[1].required_capabilities = ["weather_lookup"]
    with pytest.raises(PlanValidationError, match="Concrete runtime bindings"):
        PlanValidator().validate(
            concrete,
            task_contract=contract,
            available_capabilities={"weather.lookup"},
            forbidden_capabilities={"weather_lookup"},
        )

    with pytest.raises(PlanValidationError, match="Unavailable capabilities"):
        PlanValidator().validate(
            weather_plan(),
            task_contract=contract,
            available_capabilities=set(),
        )


async def test_plan_repository_persists_graph_and_projects_run_view(session):
    run = await RunUnitOfWork(session).create_task_run("查询天气", {"provider": "mock"}, answer_mode="trusted")
    contract = build_default_contract("查询天气")
    repository = PlanRepository(session)
    plan = await PlanService(repository).create(
        run.id,
        weather_plan(),
        contract=contract,
        capabilities={"weather.lookup"},
    )
    state = canonical_agent_state(contract, plan, policy_version=1)
    await RunUnitOfWork(session).initialize_reasoning_state(
        run.id,
        task_contract=contract.model_dump(mode="json"),
        plan_graph=plan_to_view(plan).model_dump(mode="json"),
        agent_state=state.model_dump(mode="json"),
    )

    loaded = await RunUnitOfWork(session).require_run(run.id)
    view = run_payload(loaded)
    assert view["plan_graph"]["id"] == plan.id
    assert [item["node_key"] for item in view["steps"]] == [
        "resolve-location",
        "fetch-weather",
        "answer",
    ]
    assert view["steps"][1]["depends_on"] == ["resolve-location"]
    assert loaded.agent_state["active_plan_id"] == plan.id
    assert loaded.agent_state.get("plan") is None


async def test_standard_run_never_projects_a_plan_graph(session):
    run = await RunUnitOfWork(session).create_task_run("快速回答", {"provider": "mock"})
    plan = await PlanRepository(session).create(run.id, weather_plan())
    run.plan_graph = plan_to_view(plan).model_dump(mode="json")
    await session.commit()

    view = run_payload(await RunUnitOfWork(session).require_run(run.id))
    assert view["plan_graph"] == {}
    assert view["plan_versions"] == []
    assert view["steps"] == []


async def test_plan_projection_uses_explicit_edges_and_keeps_depends_on(session):
    run = await RunUnitOfWork(session).create_task_run("图投影", {"provider": "mock"}, answer_mode="trusted")
    plan = await PlanRepository(session).create(run.id, weather_plan())

    view = plan_to_view(plan)
    node_ids = {node.node_key: node.id for node in view.nodes}
    assert view.nodes[1].depends_on == ["resolve-location"]
    assert {(edge.predecessor_node_id, edge.successor_node_id) for edge in view.edges} == {
        (node_ids["resolve-location"], node_ids["fetch-weather"]),
        (node_ids["fetch-weather"], node_ids["answer"]),
    }


async def test_plan_version_and_node_key_constraints(session):
    run = await RunUnitOfWork(session).create_task_run("约束测试", {"provider": "mock"})
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


async def test_plan_lineage_must_reference_the_same_run(session):
    first_run = await RunUnitOfWork(session).create_task_run("原运行", {"provider": "mock"})
    second_run = await RunUnitOfWork(session).create_task_run("其他运行", {"provider": "mock"})
    foreign = await PlanRepository(session).create(first_run.id, weather_plan())
    with pytest.raises(PlanStateError, match="earlier Plan"):
        await PlanRepository(session).create(
            second_run.id,
            weather_plan(),
            lineage={"resolve-location": foreign.nodes[0].id},
        )


async def test_scheduler_blocks_dependencies_and_releases_successors(session):
    run = await RunUnitOfWork(session).create_task_run("查询天气", {"provider": "mock"})
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
    run = await RunUnitOfWork(session).create_task_run("并行候选", {"provider": "mock"})
    contract = build_default_contract("并行候选")
    draft = PlanDraft(
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
    run = await RunUnitOfWork(session).create_task_run("状态转换", {"provider": "mock"})
    repository = PlanRepository(session)
    plan = await repository.create(run.id, weather_plan())
    with pytest.raises(PlanStateError, match="pending -> completed"):
        await repository.transition_node(plan.nodes[0].id, PlanNodeStatus.completed)


async def test_plan_patch_creates_version_and_preserves_lineage(session):
    run = await RunUnitOfWork(session).create_task_run("重规划", {"provider": "mock"})
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
                    expected_outcome=ExpectedObservation(kind="air_quality", success_condition="AQI available"),
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
    run = await RunUnitOfWork(session).create_task_run("过期补丁", {"provider": "mock"})
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


async def test_plan_patch_preserves_completed_nodes_and_evidence(session):
    run = await RunUnitOfWork(session).create_task_run("保留成果", {"provider": "mock"})
    contract = build_default_contract("保留成果")
    repository = PlanRepository(session)
    service = PlanService(repository)
    plan = await service.create(
        run.id,
        weather_plan(),
        contract=contract,
        capabilities={"weather.lookup"},
    )
    first = plan.nodes[0]
    await repository.transition_node(first.id, PlanNodeStatus.running)
    await repository.transition_node(first.id, PlanNodeStatus.completed, evidence_refs=["evidence-1"])
    revised = await service.apply_patch(
        run.id,
        PlanPatch(
            expected_plan_version=plan.version,
            reason="只修改未开始节点",
            operations=[
                PlanPatchOperation(
                    operation="update_node",
                    node_key="answer",
                    updates={"title": "生成最终建议"},
                )
            ],
        ),
        contract=contract,
        capabilities={"weather.lookup"},
    )
    preserved = next(node for node in revised.nodes if node.node_key == "resolve-location")
    assert preserved.status == "completed"
    assert preserved.evidence_refs == ["evidence-1"]
    assert preserved.lineage_node_id == first.id

    graph_diff = diff_plans(plan, revised)
    assert next(item for item in graph_diff.nodes if item.node_key == "resolve-location").change == "inherited_completed"
    assert next(item for item in graph_diff.nodes if item.node_key == "answer").change == "modified"


async def test_plan_patch_rejects_running_node_and_cyclic_update(session):
    run = await RunUnitOfWork(session).create_task_run("补丁保护", {"provider": "mock"})
    contract = build_default_contract("补丁保护")
    repository = PlanRepository(session)
    service = PlanService(repository)
    plan = await service.create(
        run.id,
        weather_plan(),
        contract=contract,
        capabilities={"weather.lookup"},
    )
    await repository.transition_node(plan.nodes[0].id, PlanNodeStatus.running)
    with pytest.raises(PlanStateError, match="running"):
        await service.apply_patch(
            run.id,
            PlanPatch(
                expected_plan_version=plan.version,
                reason="unsafe",
                operations=[
                    PlanPatchOperation(
                        operation="update_node",
                        node_key="answer",
                        updates={"title": "unsafe"},
                    )
                ],
            ),
            contract=contract,
            capabilities={"weather.lookup"},
        )
    cycle_run = await RunUnitOfWork(session).create_task_run("循环补丁", {"provider": "mock"})
    cycle_plan = await service.create(
        cycle_run.id,
        weather_plan(),
        contract=contract,
        capabilities={"weather.lookup"},
    )
    with pytest.raises(PlanValidationError, match="cycle"):
        await service.apply_patch(
            cycle_run.id,
            PlanPatch(
                expected_plan_version=cycle_plan.version,
                reason="cycle",
                operations=[
                    PlanPatchOperation(
                        operation="add_dependency",
                        node_key="resolve-location",
                        predecessor_key="answer",
                    )
                ],
            ),
            contract=contract,
            capabilities={"weather.lookup"},
        )
