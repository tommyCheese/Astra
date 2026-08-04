from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.db.models.executions import AgentExecutionRecord
from app.db.models.runs import RunRecord
from app.repositories.agent_executions import (
    AgentExecutionRepository,
    AgentExecutionStateError,
)
from app.repositories.conversations import ConversationRepository
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.subagents import DelegationContract, DelegationRequest


def child_contract(
    *,
    run_id: str,
    task_id: str,
    parent_execution_id: str,
    request_id: str = "child-1",
    objective: str = "Research one bounded topic",
    depth: int = 1,
) -> DelegationContract:
    request = DelegationRequest(
        request_id=request_id,
        objective=objective,
        success_criteria=["Return one verified result"],
        scope={"included": [objective], "excluded": ["unrelated work"]},
        output_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
        dedupe_key=request_id,
    )
    return DelegationContract(
        contract_id=f"contract-{request_id}",
        contract_hash=f"sha256:{request_id}",
        task_id=task_id,
        run_id=run_id,
        parent_execution_id=parent_execution_id,
        depth=depth,
        request=request,
        created_at=datetime.now(UTC),
    )


async def test_new_run_has_one_backward_compatible_root_execution(session):
    run = await RunUnitOfWork(session).create_task_run("Root execution", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)

    assert root is not None
    assert root.task_id == run.task_id
    assert root.execution_type == "root"
    assert root.root_slot == "root"
    assert root.depth == 0
    assert root.status == "queued"
    assert (
        await session.scalar(
            select(func.count(AgentExecutionRecord.id)).where(
                AgentExecutionRecord.run_id == run.id
            )
        )
        == 1
    )


async def test_reasoning_initialization_checkpoints_root_execution(session):
    runs = RunUnitOfWork(session)
    run = await runs.create_task_run("Initialize root", {})

    await runs.initialize_reasoning_state(
        run.id,
        task_contract={"original_goal": "Initialize root"},
        plan_graph={"version": 1, "nodes": []},
        agent_state={"version": 1, "budget_usage": {"model_calls": 1}},
    )
    root = await AgentExecutionRepository(session).root_for_run(run.id)

    assert root is not None
    assert root.contract == {"original_goal": "Initialize root"}
    assert root.checkpoint["version"] == 1
    assert root.budget_usage == {"model_calls": 1}
    assert root.status == "running"


async def test_child_creation_is_idempotent_and_contract_is_immutable(session):
    run = await RunUnitOfWork(session).create_task_run("Child creation", {})
    repository = AgentExecutionRepository(session)
    root = await repository.root_for_run(run.id)
    assert root is not None
    contract = child_contract(
        run_id=run.id,
        task_id=run.task_id,
        parent_execution_id=root.id,
    )

    child = await repository.create_child(contract=contract)
    same_child = await repository.create_child(contract=contract)

    assert same_child.id == child.id
    assert child.parent_execution_id == root.id
    assert child.depth == 1
    assert child.status == "queued"

    changed = child_contract(
        run_id=run.id,
        task_id=run.task_id,
        parent_execution_id=root.id,
        objective="A different task under the same request id",
    )
    with pytest.raises(AgentExecutionStateError, match="different contract"):
        await repository.create_child(contract=changed)


async def test_claim_checkpoint_heartbeat_and_transition_use_fencing(session):
    run = await RunUnitOfWork(session).create_task_run("Child lifecycle", {})
    repository = AgentExecutionRepository(session)
    root = await repository.root_for_run(run.id)
    assert root is not None
    child = await repository.create_child(
        contract=child_contract(
            run_id=run.id,
            task_id=run.task_id,
            parent_execution_id=root.id,
        )
    )

    claimed = await repository.claim(
        child.id,
        worker_id="worker-1",
        expected_state_version=child.state_version,
    )
    assert claimed.status == "running"
    assert claimed.fencing_token == 1

    await repository.heartbeat(
        child.id,
        worker_id="worker-1",
        fencing_token=claimed.fencing_token,
    )
    checkpointed = await repository.save_checkpoint(
        child.id,
        worker_id="worker-1",
        fencing_token=claimed.fencing_token,
        expected_state_version=claimed.state_version,
        checkpoint={"turn": 2},
        budget_usage={"tokens": 120},
    )
    assert checkpointed.checkpoint == {"turn": 2}
    assert checkpointed.budget_usage == {"tokens": 120}

    with pytest.raises(AgentExecutionStateError, match="Stale"):
        await repository.save_checkpoint(
            child.id,
            worker_id="worker-1",
            fencing_token=0,
            expected_state_version=checkpointed.state_version,
            checkpoint={"turn": 3},
        )

    completing = await repository.transition(
        child.id,
        expected_state_version=checkpointed.state_version,
        expected_fencing_token=claimed.fencing_token,
        status="completing",
        phase="completing",
    )
    completed = await repository.transition(
        child.id,
        expected_state_version=completing.state_version,
        expected_fencing_token=claimed.fencing_token,
        status="completed",
        phase="terminal",
        result={"status": "completed", "summary": "done"},
    )

    assert completed.status == "completed"
    assert completed.finished_at is not None
    assert completed.worker_id is None


async def test_descendant_barrier_and_stale_scan_are_scoped_to_tree(session):
    run = await RunUnitOfWork(session).create_task_run("Descendant tree", {})
    repository = AgentExecutionRepository(session)
    root = await repository.root_for_run(run.id)
    assert root is not None
    child = await repository.create_child(
        contract=child_contract(
            run_id=run.id,
            task_id=run.task_id,
            parent_execution_id=root.id,
        )
    )
    grandchild = await repository.create_child(
        contract=child_contract(
            run_id=run.id,
            task_id=run.task_id,
            parent_execution_id=child.id,
            request_id="grandchild-1",
            objective="Verify the child output",
            depth=2,
        )
    )
    claimed = await repository.claim(
        child.id, worker_id="stale-worker", expected_state_version=child.state_version
    )
    claimed.heartbeat_at = datetime.now(UTC) - timedelta(minutes=10)
    await session.flush()

    descendants = await repository.descendants(root.id)
    active = await repository.active_descendants(root.id)
    stale = await repository.stale_active(
        heartbeat_before=datetime.now(UTC) - timedelta(minutes=1)
    )

    assert [item.id for item in descendants] == [child.id, grandchild.id]
    assert {item.id for item in active} == {child.id, grandchild.id}
    assert [item.id for item in stale] == [child.id]


async def test_conversation_deletion_removes_agent_execution_tree(session):
    run = await RunUnitOfWork(session).create_task_run("Delete lineage", {})
    repository = AgentExecutionRepository(session)
    root = await repository.root_for_run(run.id)
    assert root is not None
    child = await repository.create_child(
        contract=child_contract(
            run_id=run.id,
            task_id=run.task_id,
            parent_execution_id=root.id,
        )
    )
    run.status = "completed"
    await session.commit()
    task = await ConversationRepository(session).get(run.task_id, detailed=True)
    assert task is not None

    await ConversationRepository(session).delete(task)

    assert await session.get(RunRecord, run.id) is None
    assert await session.get(AgentExecutionRecord, child.id) is None
