import asyncio
import time
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base, NodeExecutionRecord, utc_now
from app.repositories.executions import (
    NodeExecutionRepository,
    NodeExecutionStateError,
)
from app.repositories.plans import PlanRepository
from app.repositories.runs import RunRepository, run_to_view
from app.runner.concurrency import (
    ResourceClaim,
    acquire_resource_claims,
    resource_claims_conflict,
    resource_claims_from_effect_plan,
)
from app.runner.coordinator import NodeExecutionResult, RunCoordinator
from app.runner.planning import PlanScheduler, PlanService
from app.runner.reasoning import build_default_contract
from app.runner.recovery import ExecutionRecovery
from app.schemas.agent import (
    AgentState,
    ExpectedObservation,
    NodeExecutionPhase,
    NodeExecutionStatus,
    PlanDraft,
    PlanNodeDraft,
    PlanPatch,
    PlanPatchOperation,
)
from app.schemas.permissions import ActionEffectPlan, EffectItem, EffectKind


def parallel_plan() -> PlanDraft:
    return PlanDraft(
        nodes=[
            PlanNodeDraft(
                node_key="root-a",
                title="Root A",
                intent="Execute A",
                expected_outcome=ExpectedObservation(kind="result", success_condition="A complete"),
            ),
            PlanNodeDraft(
                node_key="root-b",
                title="Root B",
                intent="Execute B",
                expected_outcome=ExpectedObservation(kind="result", success_condition="B complete"),
            ),
            PlanNodeDraft(
                node_key="join",
                title="Join",
                intent="Join A and B",
                depends_on=["root-a", "root-b"],
                expected_outcome=ExpectedObservation(kind="result", success_condition="joined"),
            ),
        ]
    )


async def test_execution_lease_budget_round_trip_and_run_projection(session):
    run = await RunRepository(session).create_task_run(
        "parallel", {"provider": "mock"}, answer_mode="trusted"
    )
    plan = await PlanRepository(session).create(run.id, parallel_plan())
    repository = NodeExecutionRepository(session)
    execution = await repository.create_claim(
        run_id=run.id,
        plan_id=plan.id,
        plan_version=plan.version,
        plan_node_id=plan.nodes[0].id,
        dispatch_batch_id="batch-1",
    )
    lease = await repository.create_lease(
        run_id=run.id,
        execution_id=execution.id,
        resource_key="workspace:docs/report.md",
        resource_summary="workspace file",
        mode="write",
    )
    await repository.reserve_budgets(
        run_id=run.id,
        execution_id=execution.id,
        reservations={"turns": 2, "tool_calls": 1},
    )
    await session.commit()

    loaded = await repository.require(execution.id)
    assert loaded.plan_version == 1
    assert loaded.attempt == 1
    assert loaded.resource_leases[0].fencing_token == lease.fencing_token == 1
    assert {item.budget_kind for item in loaded.budget_reservations} == {
        "turns",
        "tool_calls",
    }

    projected = run_to_view(await RunRepository(session).require_run(run.id))
    assert projected["node_executions"][0]["execution_id"] == execution.id
    assert (
        projected["node_executions"][0]["resource_leases"][0]["resource_summary"]
        == "workspace file"
    )
    assert projected["parallelism"]["active_count"] == 1


async def test_only_one_current_execution_attempt_is_allowed(session):
    run = await RunRepository(session).create_task_run("attempts", {"provider": "mock"})
    plan = await PlanRepository(session).create(run.id, parallel_plan())
    repository = NodeExecutionRepository(session)
    first = await repository.create_claim(
        run_id=run.id,
        plan_id=plan.id,
        plan_version=plan.version,
        plan_node_id=plan.nodes[0].id,
    )
    first_id = first.id
    run_id = run.id
    plan_id = plan.id
    plan_version = plan.version
    plan_node_id = plan.nodes[0].id
    await session.commit()
    duplicate = NodeExecutionRecord(
        run_id=run.id,
        plan_id=plan.id,
        plan_version=plan.version,
        plan_node_id=plan.nodes[0].id,
        attempt=2,
        dispatch_batch_id="batch-2",
        current_slot="current",
    )
    session.add(duplicate)
    with pytest.raises(IntegrityError):
        await session.flush()
    await session.rollback()

    first = await repository.require(first_id)
    await repository.transition(
        first.id,
        expected_version=first.state_version,
        phase=NodeExecutionPhase.failed,
        status=NodeExecutionStatus.failed,
        failure={"category": "test"},
    )
    retry = await repository.create_claim(
        run_id=run_id,
        plan_id=plan_id,
        plan_version=plan_version,
        plan_node_id=plan_node_id,
    )
    assert retry.attempt == 2


async def test_execution_transition_uses_state_version_compare_and_swap(session):
    run = await RunRepository(session).create_task_run("cas", {"provider": "mock"})
    plan = await PlanRepository(session).create(run.id, parallel_plan())
    repository = NodeExecutionRepository(session)
    execution = await repository.create_claim(
        run_id=run.id,
        plan_id=plan.id,
        plan_version=plan.version,
        plan_node_id=plan.nodes[0].id,
    )
    running = await repository.transition(
        execution.id,
        expected_version=1,
        phase=NodeExecutionPhase.running,
    )
    assert running.state_version == 2
    with pytest.raises(NodeExecutionStateError):
        await repository.transition(
            execution.id,
            expected_version=1,
            phase=NodeExecutionPhase.completed,
        )


def test_obsolete_active_node_field_is_rejected():
    with pytest.raises(ValueError, match="active_node_id"):
        AgentState.model_validate(
            {
                "version": 1,
                "task_contract": {"original_goal": "obsolete"},
                "active_plan_version": 4,
                "active_node_id": "node-obsolete",
            }
        )


async def test_scheduler_claims_independent_nodes_as_one_batch(session):
    run = await RunRepository(session).create_task_run("batch", {"provider": "mock"})
    plan = await PlanRepository(session).create(run.id, parallel_plan())
    scheduler = PlanScheduler(PlanRepository(session))

    candidates = scheduler.ready_candidates(plan)
    assert [(item.node.node_key, item.dependency_rank) for item in candidates] == [
        ("root-a", 1),
        ("root-b", 1),
    ]
    batch = await scheduler.claim_ready_batch(run.id)
    assert batch is not None
    assert len(batch.executions) == 2
    assert len({item.dispatch_batch_id for item in batch.executions}) == 1
    assert batch.total_slots == 3
    assert batch.used_slots == 2

    no_more = await scheduler.claim_ready_batch(run.id)
    assert no_more is None


async def test_scheduler_single_slot_matches_serial_order(session):
    run = await RunRepository(session).create_task_run("serial", {"provider": "mock"})
    await PlanRepository(session).create(run.id, parallel_plan())
    scheduler = PlanScheduler(
        PlanRepository(session),
        server_max_parallel_nodes=1,
    )
    selected = await scheduler.select_next(run.id)
    assert selected is not None
    assert selected.node_key == "root-a"
    loaded = await RunRepository(session).require_run(run.id)
    assert len(loaded.agent_state["active_executions"]) == 1
    assert loaded.agent_state["active_executions"][0]["plan_node_id"] == selected.id


async def test_scheduler_respects_budget_and_capability_limits(session):
    run = await RunRepository(session).create_task_run("limits", {"provider": "mock"})
    draft = parallel_plan()
    draft.nodes[0].required_capabilities = ["provider:search"]
    draft.nodes[1].required_capabilities = ["provider:search"]
    await PlanRepository(session).create(run.id, draft)
    run.reasoning_policy = {
        "effective": {
            "budgets": {
                "max_parallel_nodes": 3,
                "max_turns": 2,
                "max_tool_calls": 2,
                "max_model_calls": 2,
            }
        }
    }
    await session.commit()

    scheduler = PlanScheduler(
        PlanRepository(session),
        provider_concurrency_limit=1,
    )
    first = await scheduler.claim_ready_batch(run.id)
    assert first is not None
    assert len(first.executions) == 1

    loaded_plan = await PlanRepository(session).active_for_run(run.id)
    assert loaded_plan is not None
    first_node = next(node for node in loaded_plan.nodes if node.status == "running")
    assert first_node.node_key == "root-a"
    await NodeExecutionRepository(session).transition(
        first.executions[0].id,
        expected_version=first.executions[0].state_version,
        phase=NodeExecutionPhase.completed,
        status=NodeExecutionStatus.completed,
    )
    first_node.status = "completed"
    await session.commit()

    second = await scheduler.claim_ready_batch(run.id)
    assert second is not None
    assert len(second.executions) == 1

    await NodeExecutionRepository(session).transition(
        second.executions[0].id,
        expected_version=second.executions[0].state_version,
        phase=NodeExecutionPhase.completed,
        status=NodeExecutionStatus.completed,
    )
    loaded_plan = await PlanRepository(session).active_for_run(run.id)
    assert loaded_plan is not None
    next(
        node for node in loaded_plan.nodes if node.id == second.executions[0].plan_node_id
    ).status = "completed"
    await session.commit()
    assert await scheduler.claim_ready_batch(run.id) is None


async def test_coordinator_workers_overlap_and_keep_execution_ownership(tmp_path):
    database_path = tmp_path / "parallel.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as setup_session:
        run = await RunRepository(setup_session).create_task_run("overlap", {"provider": "mock"})
        await PlanRepository(setup_session).create(run.id, parallel_plan())
        run_id = run.id
        await setup_session.commit()

    active_workers = 0
    peak_workers = 0
    first_batch_started = asyncio.Event()
    release_first_batch = asyncio.Event()
    first_batch_count = 0

    async def controlled_executor(repo, context):
        nonlocal active_workers, peak_workers, first_batch_count
        active_workers += 1
        peak_workers = max(peak_workers, active_workers)
        try:
            if context.node["node_key"].startswith("root-"):
                first_batch_count += 1
                if first_batch_count == 2:
                    first_batch_started.set()
                await release_first_batch.wait()
            await repo.create_agent_turn(
                context.run_id,
                context.node["index"],
                "complete_node",
                f"complete {context.node['node_key']}",
                plan_node_id=context.plan_node_id,
                node_execution_id=context.execution_id,
            )
            return NodeExecutionResult(
                execution_id=context.execution_id,
                plan_node_id=context.plan_node_id,
                plan_version=context.plan_version,
                attempt=context.attempt,
                evidence_refs=[f"evidence:{context.node['node_key']}"],
                budget_consumed={"turns": 1},
            )
        finally:
            active_workers -= 1

    coordinator = RunCoordinator(
        session_factory,
        server_max_parallel_nodes=3,
        heartbeat_seconds=0.05,
    )
    started = time.monotonic()
    coordinator_task = asyncio.create_task(coordinator.run(run_id, controlled_executor))
    started_task = asyncio.create_task(first_batch_started.wait())
    done, _ = await asyncio.wait(
        {coordinator_task, started_task},
        timeout=2,
        return_when=asyncio.FIRST_COMPLETED,
    )
    if coordinator_task in done:
        coordinator_task.result()
    assert started_task in done
    assert peak_workers == 2
    release_first_batch.set()
    result = await asyncio.wait_for(coordinator_task, timeout=2)
    elapsed = time.monotonic() - started

    assert result.peak_concurrency == 2
    assert peak_workers == 2
    assert len(result.completed_execution_ids) == 3
    assert elapsed < 1
    async with session_factory() as verification_session:
        loaded = await RunRepository(verification_session).require_run(run_id)
        assert {node.status for plan in loaded.plans for node in plan.nodes} == {"completed"}
        assert len(loaded.node_executions) == 3
        assert all(item.status == "completed" for item in loaded.node_executions)
        assert {turn.node_execution_id for turn in loaded.turns} == {
            item.id for item in loaded.node_executions
        }
    await engine.dispose()


def test_resource_claims_support_hierarchical_reads_and_writes():
    parent_read = ResourceClaim("workspace://task/docs", "docs", "read")
    child_read = ResourceClaim("workspace://task/docs/a.md", "file", "read")
    child_write = ResourceClaim("workspace://task/docs/a.md", "file", "write")
    unrelated_write = ResourceClaim("workspace://task/images/a.png", "image", "write")

    assert not resource_claims_conflict(parent_read, child_read)
    assert resource_claims_conflict(parent_read, child_write)
    assert not resource_claims_conflict(child_write, unrelated_write)


def test_unknown_and_non_idempotent_effects_become_safe_exclusive_claims():
    plan = ActionEffectPlan(
        tool_name="external.publish",
        tool_version="1",
        summary="publish",
        effects=[
            EffectItem(
                kind=EffectKind.external_write,
                resource="external://unknown",
                reversible=False,
                persistent=True,
            )
        ],
        analyzer_version="1",
    )
    claims = resource_claims_from_effect_plan(plan)
    assert claims[0].mode == "exclusive"
    assert claims[0].resource_key == "provider://external.publish/"
    assert "external.publish" not in claims[0].resource_summary


async def test_resource_leases_allow_reads_and_reject_hierarchical_write_conflicts(
    session,
):
    run = await RunRepository(session).create_task_run("leases", {"provider": "mock"})
    plan = await PlanRepository(session).create(run.id, parallel_plan())
    repository = NodeExecutionRepository(session)
    first = await repository.create_claim(
        run_id=run.id,
        plan_id=plan.id,
        plan_version=plan.version,
        plan_node_id=plan.nodes[0].id,
    )
    second = await repository.create_claim(
        run_id=run.id,
        plan_id=plan.id,
        plan_version=plan.version,
        plan_node_id=plan.nodes[1].id,
    )
    assert await acquire_resource_claims(
        repository,
        run_id=run.id,
        execution_id=first.id,
        claims=(ResourceClaim("workspace://task/docs", "docs", "read"),),
    )
    assert await acquire_resource_claims(
        repository,
        run_id=run.id,
        execution_id=second.id,
        claims=(ResourceClaim("workspace://task/docs/a.md", "file", "read"),),
    )
    await repository.release_leases(second.id, reason="test")
    assert not await acquire_resource_claims(
        repository,
        run_id=run.id,
        execution_id=second.id,
        claims=(ResourceClaim("workspace://task/docs/a.md", "file", "write"),),
    )


async def test_run_cancellation_terminates_executions_and_releases_reservations(
    session,
):
    run = await RunRepository(session).create_task_run("cancel", {"provider": "mock"})
    plan = await PlanRepository(session).create(run.id, parallel_plan())
    repository = NodeExecutionRepository(session)
    execution = await repository.create_claim(
        run_id=run.id,
        plan_id=plan.id,
        plan_version=plan.version,
        plan_node_id=plan.nodes[0].id,
        slot_index=0,
    )
    await repository.reserve_budgets(
        run_id=run.id,
        execution_id=execution.id,
        reservations={"turns": 1},
    )
    await repository.create_lease(
        run_id=run.id,
        execution_id=execution.id,
        resource_key="workspace://task/file",
        resource_summary="workspace file",
        mode="write",
    )
    await session.commit()

    await RunRepository(session).cancel_run(run.id)
    loaded = await repository.require(execution.id)
    assert loaded.status == "cancelled"
    assert loaded.current_slot is None
    assert loaded.slot_index is None
    assert loaded.resource_leases[0].release_reason == "user_cancelled"
    assert loaded.budget_reservations[0].status == "cancelled"
    cancelled_run = await RunRepository(session).require_run(run.id)
    cancelled_event = next(
        event for event in cancelled_run.events if event.type == "plan.node.execution_cancelled"
    )
    assert cancelled_event.payload["node_execution_id"] == execution.id
    assert cancelled_event.payload["attempt"] == 1


async def test_failure_blocks_all_descendants_but_not_unrelated_branch(tmp_path):
    database_path = tmp_path / "failure-scope.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    draft = PlanDraft(
        nodes=[
            PlanNodeDraft(
                node_key="failed-root",
                title="Root",
                intent="fail",
                expected_outcome=ExpectedObservation(kind="result", success_condition="root"),
            ),
            PlanNodeDraft(
                node_key="child",
                title="Child",
                intent="blocked",
                depends_on=["failed-root"],
                expected_outcome=ExpectedObservation(kind="result", success_condition="child"),
            ),
            PlanNodeDraft(
                node_key="grandchild",
                title="Grandchild",
                intent="also blocked",
                depends_on=["child"],
                expected_outcome=ExpectedObservation(kind="result", success_condition="grandchild"),
            ),
            PlanNodeDraft(
                node_key="independent",
                title="Independent",
                intent="finish",
                expected_outcome=ExpectedObservation(
                    kind="result", success_condition="independent"
                ),
            ),
        ]
    )
    async with session_factory() as session:
        run = await RunRepository(session).create_task_run("failure scope", {"provider": "mock"})
        await PlanRepository(session).create(run.id, draft)
        run_id = run.id
        await session.commit()

    async def executor(repo, context):
        del repo
        failed = context.node["node_key"] == "failed-root"
        return NodeExecutionResult(
            execution_id=context.execution_id,
            plan_node_id=context.plan_node_id,
            plan_version=context.plan_version,
            attempt=context.attempt,
            status=(NodeExecutionStatus.failed if failed else NodeExecutionStatus.completed),
            failure={"category": "permanent"} if failed else None,
        )

    await RunCoordinator(session_factory).run(run_id, executor)
    async with session_factory() as session:
        plan = await PlanRepository(session).active_for_run(run_id)
        assert plan is not None
        statuses = {node.node_key: node.status for node in plan.nodes}
        assert statuses == {
            "failed-root": "failed",
            "child": "blocked",
            "grandchild": "blocked",
            "independent": "completed",
        }
    await engine.dispose()


async def test_safe_timeout_creates_one_new_attempt_and_reuses_dag_position(tmp_path):
    database_path = tmp_path / "timeout-retry.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        run = await RunRepository(session).create_task_run("retry", {"provider": "mock"})
        await PlanRepository(session).create(
            run.id,
            PlanDraft(
                nodes=[
                    PlanNodeDraft(
                        node_key="read-only",
                        title="Read",
                        intent="retry safely",
                        expected_outcome=ExpectedObservation(
                            kind="result", success_condition="read"
                        ),
                    )
                ]
            ),
        )
        run_id = run.id
        await session.commit()

    async def executor(repo, context):
        del repo
        if context.attempt == 1:
            await asyncio.sleep(0.2)
        return NodeExecutionResult(
            execution_id=context.execution_id,
            plan_node_id=context.plan_node_id,
            plan_version=context.plan_version,
            attempt=context.attempt,
        )

    result = await RunCoordinator(
        session_factory,
        attempt_timeout_seconds=0.03,
        max_safe_retries=1,
    ).run(run_id, executor)
    assert len(result.completed_execution_ids) == 1
    async with session_factory() as session:
        executions = await NodeExecutionRepository(session).list_for_run(run_id)
        assert [(item.attempt, item.status) for item in executions] == [
            (1, "failed"),
            (2, "completed"),
        ]
        assert executions[0].failure["category"] == "attempt_timeout"
    await engine.dispose()


async def test_recovery_classifies_resume_replay_and_unknown_outcomes(tmp_path):
    database_path = tmp_path / "recovery.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        run = await RunRepository(session).create_task_run("recover", {"provider": "mock"})
        plan = await PlanRepository(session).create(run.id, parallel_plan())
        repository = NodeExecutionRepository(session)
        resume = await repository.create_claim(
            run_id=run.id,
            plan_id=plan.id,
            plan_version=plan.version,
            plan_node_id=plan.nodes[0].id,
        )
        replay = await repository.create_claim(
            run_id=run.id,
            plan_id=plan.id,
            plan_version=plan.version,
            plan_node_id=plan.nodes[1].id,
        )
        replay.phase = NodeExecutionPhase.committing.value
        replay.result = NodeExecutionResult(
            execution_id=replay.id,
            plan_node_id=replay.plan_node_id,
            plan_version=replay.plan_version,
            attempt=replay.attempt,
        ).model_dump(mode="json")
        unknown = await repository.create_claim(
            run_id=run.id,
            plan_id=plan.id,
            plan_version=plan.version,
            plan_node_id=plan.nodes[2].id,
        )
        unknown.checkpoint = {"action_started": True, "idempotent": False}
        old = utc_now() - timedelta(seconds=10)
        for execution in (resume, replay, unknown):
            execution.heartbeat_at = old
        run_id = run.id
        await session.commit()

    scan = await ExecutionRecovery(session_factory, stale_seconds=1).scan(run_id)
    assert scan.resumable_execution_ids == (resume.id,)
    assert scan.replayable_execution_ids == (replay.id,)
    assert scan.unknown_execution_ids == (unknown.id,)
    async with session_factory() as session:
        repository = NodeExecutionRepository(session)
        resumed = await repository.require(resume.id)
        uncertain = await repository.require(unknown.id)
        assert resumed.phase == "claimed"
        assert resumed.worker_id is None
        assert uncertain.phase == "result_unknown"
        assert uncertain.wait_reason == "non_idempotent_result_unknown"
    await engine.dispose()


async def test_concurrent_schedulers_cannot_overclaim_run_slots(tmp_path):
    database_path = tmp_path / "scheduler-race.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        run = await RunRepository(session).create_task_run("race", {"provider": "mock"})
        await PlanRepository(session).create(run.id, parallel_plan())
        run_id = run.id
        await session.commit()

    async def claim():
        async with session_factory() as session:
            batch = await PlanScheduler(
                PlanRepository(session),
                server_max_parallel_nodes=1,
            ).claim_ready_batch(run_id)
            await session.commit()
            return batch

    batches = await asyncio.gather(claim(), claim())
    claimed = [
        execution.id for batch in batches if batch is not None for execution in batch.executions
    ]
    assert len(claimed) == 1
    async with session_factory() as session:
        active = await NodeExecutionRepository(session).active_for_run(run_id)
        assert len(active) == 1
        assert active[0].slot_index == 0
    await engine.dispose()


async def test_replan_drains_owned_attempt_before_activating_new_version(session):
    run = await RunRepository(session).create_task_run("replan drain", {"provider": "mock"})
    repository = PlanRepository(session)
    plan = await repository.create(run.id, parallel_plan())
    selected = await PlanScheduler(
        repository,
        server_max_parallel_nodes=1,
    ).select_next(run.id)
    assert selected is not None

    revised = await PlanService(repository).apply_patch(
        run.id,
        PlanPatch(
            expected_plan_version=plan.version,
            reason="replace active branch safely",
            operations=[
                PlanPatchOperation(
                    operation="update_node",
                    node_key=selected.node_key,
                    updates={"title": "Revised root"},
                )
            ],
        ),
        contract=build_default_contract("replan drain"),
    )

    assert revised.version == 2
    assert revised.status == "active"
    loaded = await RunRepository(session).require_run(run.id)
    assert loaded.status == "executing"
    assert loaded.active_plan_id == revised.id
    old_execution = loaded.node_executions[0]
    assert old_execution.status == "cancelled"
    assert old_execution.failure == {"category": "replanned"}
    assert loaded.agent_state["active_executions"] == []
    assert any(event.type == "plan.revision.draining" for event in loaded.events)
