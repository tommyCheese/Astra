from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.subagents.budget import (
    DelegationGateInput,
    HierarchicalBudgetError,
    HierarchicalBudgetManager,
    evaluate_delegation,
)
from app.application.subagents.coordinator import AgentCoordinator, HierarchicalSemaphoreRegistry
from app.common.schemas.subagents import (
    DelegationContract,
    DelegationRequest,
    DelegationScope,
    SubagentBudgetEnvelope,
    SubagentExecutionStatus,
)
from app.infrastructure.db.model_base import AstraOrmRecordBase
from app.infrastructure.db.models.executions import (
    AgentBudgetReservationRecord,
    AgentExecutionRecord,
)
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _contract(root, *, request_id: str, tokens: int = 60) -> DelegationContract:
    return DelegationContract(
        contract_id=f"dc-{request_id}",
        contract_hash=f"sha256:{request_id}",
        task_id=root.task_id,
        run_id=root.run_id,
        parent_execution_id=root.id,
        depth=root.depth + 1,
        request=DelegationRequest(
            request_id=request_id,
            objective=f"Independent work {request_id}",
            success_criteria=["Return a result"],
            scope=DelegationScope(included=[f"scope:{request_id}"]),
            output_schema={"type": "object", "properties": {}},
            budget=SubagentBudgetEnvelope(
                max_tokens=tokens,
                max_model_calls=4,
                max_tool_calls=4,
                max_wall_time_ms=60_000,
                max_cost_usd=0.4,
            ),
            dedupe_key=request_id,
        ),
        created_at=NOW,
    )


async def _database(tmp_path, name: str):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / name}",
        future=True,
    )
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def test_atomic_hierarchical_budget_race_preserves_parent_reserve(tmp_path):
    engine, sessions = await _database(tmp_path, "budget-race.db")
    async with sessions() as session:
        run = await RunUnitOfWork(session).create_task_run("Budget race", {})
        root = await AgentExecutionRepository(session).root_for_run(run.id)
        root.budget_envelope = {
            "max_tokens": 100,
            "max_model_calls": 10,
            "max_tool_calls": 10,
            "max_wall_time_ms": 120_000,
            "max_cost_usd": 1,
            "max_children": 2,
        }
        first = await AgentExecutionRepository(session).create_child(
            contract=_contract(root, request_id="first")
        )
        second = await AgentExecutionRepository(session).create_child(
            contract=_contract(root, request_id="second")
        )
        await session.commit()

    async def reserve(child_id: str):
        async with sessions() as session:
            return await HierarchicalBudgetManager(
                session, parent_reserve={"tokens": 20}
            ).reserve(
                parent_execution_id=root.id,
                child_execution_id=child_id,
                envelope=SubagentBudgetEnvelope(
                    max_tokens=60,
                    max_model_calls=4,
                    max_tool_calls=4,
                    max_wall_time_ms=60_000,
                    max_cost_usd=0.4,
                ),
                max_children_total=2,
                max_children_per_parent=2,
                max_parallel_children=2,
            )

    outcomes = await asyncio.gather(
        reserve(first.id), reserve(second.id), return_exceptions=True
    )
    successes = [item for item in outcomes if isinstance(item, AgentBudgetReservationRecord)]
    failures = [item for item in outcomes if isinstance(item, BaseException)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], HierarchicalBudgetError)
    async with sessions() as session:
        persisted_root = await session.get(AgentExecutionRecord, root.id)
        reservations = list(
            (
                await session.scalars(
                    select(AgentBudgetReservationRecord).where(
                        AgentBudgetReservationRecord.status == "reserved"
                    )
                )
            ).all()
        )
        assert len(reservations) == 1
        assert persisted_root.budget_usage["delegated_reserved"]["tokens"] == 60
    await engine.dispose()


async def test_budget_settlement_is_exact_once_and_returns_unused_capacity(session):
    run = await RunUnitOfWork(session).create_task_run("Budget settlement", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    root.budget_envelope = {
        "max_tokens": 100,
        "max_model_calls": 10,
        "max_tool_calls": 10,
        "max_wall_time_ms": 120_000,
        "max_cost_usd": 1,
        "max_children": 2,
    }
    child = await AgentExecutionRepository(session).create_child(
        contract=_contract(root, request_id="settle")
    )
    await session.commit()
    manager = HierarchicalBudgetManager(session, parent_reserve={"tokens": 20})
    await manager.reserve(
        parent_execution_id=root.id,
        child_execution_id=child.id,
        envelope=child.budget_envelope,
        max_children_total=2,
        max_children_per_parent=2,
        max_parallel_children=2,
    )
    settled = await manager.settle(
        child.id,
        actual_usage={
            "tokens": 25,
            "model_calls": 2,
            "tool_calls": 1,
            "wall_time_ms": 1_000,
            "cost_usd": 0.1,
        },
    )
    same = await manager.settle(
        child.id,
        actual_usage={
            "tokens": 25,
            "model_calls": 2,
            "tool_calls": 1,
            "wall_time_ms": 1_000,
            "cost_usd": 0.1,
        },
    )
    await session.refresh(root)
    assert settled.id == same.id
    assert settled.returned_budget["tokens"] == 35
    assert root.budget_usage["delegated_reserved"]["tokens"] == 0
    assert root.budget_usage["descendant_usage"]["tokens"] == 25
    with pytest.raises(HierarchicalBudgetError, match="already settled differently"):
        await manager.settle(child.id, actual_usage={"tokens": 26})


async def test_agent_coordinator_bounds_parallelism_and_dynamic_node_allowance(tmp_path):
    engine, sessions = await _database(tmp_path, "agent-coordinator.db")
    async with sessions() as session:
        run = await RunUnitOfWork(session).create_task_run("Coordinator", {})
        root = await AgentExecutionRepository(session).root_for_run(run.id)
        children = [
            await AgentExecutionRepository(session).create_child(
                contract=_contract(root, request_id=f"child-{index}", tokens=10)
            )
            for index in range(4)
        ]
        await session.commit()

    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker(session, execution, fencing_token):
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.05)
        repository = AgentExecutionRepository(session)
        current = await repository.require(execution.id)
        current = await repository.transition(
            current.id,
            expected_state_version=current.state_version,
            expected_fencing_token=fencing_token,
            status=SubagentExecutionStatus.completing,
            phase="completing",
        )
        await repository.transition(
            current.id,
            expected_state_version=current.state_version,
            expected_fencing_token=fencing_token,
            status=SubagentExecutionStatus.completed,
            phase="terminal",
            result={"status": "completed"},
        )
        async with lock:
            active -= 1

    registry = HierarchicalSemaphoreRegistry()
    coordinator = AgentCoordinator(
        sessions,
        deployment_max_agents=2,
        run_max_agents=2,
        run_max_nodes=4,
        heartbeat_seconds=0.02,
        semaphore_registry=registry,
    )
    first = await coordinator.run_available(
        run.id,
        worker,
        provider="test-provider",
        provider_limit=2,
    )
    second = await coordinator.run_available(
        run.id,
        worker,
        provider="test-provider",
        provider_limit=2,
    )

    assert first.peak_concurrency == 2
    assert first.dynamic_node_allowance == 2
    assert len(first.queued_ids) == 2
    assert len(first.completed_ids) == 2
    assert len(second.completed_ids) == 2
    assert peak == 2
    async with sessions() as session:
        statuses = list(
            (
                await session.scalars(
                    select(AgentExecutionRecord.status).where(
                        AgentExecutionRecord.id.in_([item.id for item in children])
                    )
                )
            ).all()
        )
        assert statuses == ["completed"] * 4
    await engine.dispose()


async def test_provider_semaphore_prevents_child_node_slot_multiplication(tmp_path):
    engine, sessions = await _database(tmp_path, "provider-cap.db")
    async with sessions() as session:
        run = await RunUnitOfWork(session).create_task_run("Provider cap", {})
        root = await AgentExecutionRepository(session).root_for_run(run.id)
        for index in range(3):
            await AgentExecutionRepository(session).create_child(
                contract=_contract(root, request_id=f"provider-{index}", tokens=10)
            )
        await session.commit()
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker(session, execution, fencing_token):
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.02)
        repository = AgentExecutionRepository(session)
        current = await repository.require(execution.id)
        current = await repository.transition(
            current.id,
            expected_state_version=current.state_version,
            expected_fencing_token=fencing_token,
            status="completing",
            phase="completing",
        )
        await repository.transition(
            current.id,
            expected_state_version=current.state_version,
            expected_fencing_token=fencing_token,
            status="completed",
            phase="terminal",
        )
        async with lock:
            active -= 1

    result = await AgentCoordinator(
        sessions,
        deployment_max_agents=3,
        run_max_agents=3,
        run_max_nodes=6,
        semaphore_registry=HierarchicalSemaphoreRegistry(),
    ).run_available(
        run.id,
        worker,
        provider="single-provider",
        provider_limit=1,
    )
    assert len(result.completed_ids) == 3
    assert result.peak_concurrency == 1
    assert peak == 1
    assert result.dynamic_node_allowance == 2
    await engine.dispose()


def test_adaptive_delegation_gate_accepts_breadth_and_rejects_negative_cases():
    breadth = evaluate_delegation(
        DelegationGateInput(
            complexity=0.9,
            independence=0.95,
            context_pressure=0.7,
            estimated_benefit=0.9,
            write_conflict_risk=0.05,
            execution_risk=0.1,
            budget_fraction_remaining=0.8,
        )
    )
    simple = evaluate_delegation(
        DelegationGateInput(
            complexity=0.2,
            independence=0.2,
            context_pressure=0.1,
            estimated_benefit=0.1,
            write_conflict_risk=0,
            execution_risk=0,
            budget_fraction_remaining=1,
            simple_atomic=True,
        )
    )
    conflict = evaluate_delegation(
        DelegationGateInput(
            complexity=1,
            independence=0.8,
            context_pressure=0.8,
            estimated_benefit=1,
            write_conflict_risk=0.9,
            execution_risk=0.2,
            budget_fraction_remaining=1,
        )
    )
    assert breadth.allowed is True
    assert simple.reason_code == "delegation_not_beneficial_simple"
    assert conflict.reason_code == "delegation_write_conflict"


async def test_configured_maximum_child_load_remains_bounded(tmp_path):
    engine, sessions = await _database(tmp_path, "bounded-load.db")
    async with sessions() as session:
        run = await RunUnitOfWork(session).create_task_run("Bounded load", {})
        root = await AgentExecutionRepository(session).root_for_run(run.id)
        for index in range(12):
            await AgentExecutionRepository(session).create_child(
                contract=_contract(root, request_id=f"load-{index}", tokens=5)
            )
        await session.commit()
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def worker(session, execution, fencing_token):
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.005)
        repository = AgentExecutionRepository(session)
        current = await repository.require(execution.id)
        current = await repository.transition(
            current.id,
            expected_state_version=current.state_version,
            expected_fencing_token=fencing_token,
            status="completing",
            phase="completing",
        )
        await repository.transition(
            current.id,
            expected_state_version=current.state_version,
            expected_fencing_token=fencing_token,
            status="completed",
            phase="terminal",
        )
        async with lock:
            active -= 1

    coordinator = AgentCoordinator(
        sessions,
        deployment_max_agents=2,
        run_max_agents=3,
        run_max_nodes=6,
        semaphore_registry=HierarchicalSemaphoreRegistry(),
    )
    batches = []
    for _ in range(4):
        batches.append(
            await coordinator.run_available(
                run.id,
                worker,
                provider="load-provider",
                provider_limit=2,
                tool_group="read-tools",
                tool_limit=2,
                capability="research",
                capability_limit=2,
            )
        )
    assert sum(len(item.completed_ids) for item in batches) == 12
    assert max(item.peak_concurrency for item in batches) <= 2
    assert peak <= 2
    assert batches[0].dynamic_node_allowance == 2
    await engine.dispose()
