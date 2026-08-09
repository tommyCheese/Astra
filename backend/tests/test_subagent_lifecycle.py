from datetime import UTC, datetime, timedelta

import pytest

from app.application.subagents.lifecycle import SubagentCancellationService
from app.application.subagents.recovery import SubagentExecutionRecovery
from app.common.schemas.subagents import DelegationContract, DelegationRequest
from app.infrastructure.db.models.executions import AgentJoinRecord
from app.infrastructure.db.models.permissions import (
    AgentDelegationRecord,
    AgentIdentityRecord,
    ToolCallRecord,
)
from app.infrastructure.repositories.agent_executions import (
    AgentExecutionRepository,
    AgentExecutionStateError,
)
from app.infrastructure.repositories.approval_contracts import ApprovalRequestCreate
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.run_view_projection import run_payload


def _contract(run, root, request_id: str) -> DelegationContract:
    return DelegationContract(
        contract_id=f"contract-{request_id}",
        contract_hash=f"sha256:{request_id}",
        task_id=run.task_id,
        run_id=run.id,
        parent_execution_id=root.id,
        depth=1,
        request=DelegationRequest(
            request_id=request_id,
            objective="Perform bounded delegated work",
            success_criteria=["Return a result"],
            scope={"included": ["bounded work"]},
            output_schema={"type": "object"},
            dedupe_key=request_id,
        ),
        created_at=datetime.now(UTC),
    )


async def _child(session, request_id: str = "child"):
    run = await RunUnitOfWork(session).create_task_run("Lifecycle", {})
    executions = AgentExecutionRepository(session)
    root = await executions.root_for_run(run.id)
    assert root is not None
    child = await executions.create_child(contract=_contract(run, root, request_id))
    await session.commit()
    return run, root, child


async def test_cancellation_fences_worker_terminates_sandbox_and_reports_effects(session):
    run, _, child = await _child(session)
    executions = AgentExecutionRepository(session)
    child = await executions.claim(
        child.id,
        worker_id="old-worker",
        expected_state_version=child.state_version,
        expected_cancellation_epoch=child.cancellation_epoch,
    )
    old_version = child.state_version
    old_fence = child.fencing_token
    old_epoch = child.cancellation_epoch
    runs = RunUnitOfWork(session)
    uncertain = await runs.start_tool_call(
        run.id,
        None,
        "external.write",
        "1",
        {"value": 1},
        "external_write",
        "external_write",
        agent_execution_id=child.id,
    )
    committed = await runs.start_tool_call(
        run.id,
        None,
        "external.write",
        "1",
        {"value": 2},
        "external_write",
        "external_write",
        agent_execution_id=child.id,
    )
    committed.status = "succeeded"
    committed.output = {"remote_id": "immutable-1"}
    committed.completed_at = datetime.now(UTC)
    job = await runs.create_sandbox_job(
        run.id,
        tool_call_id=uncertain.id,
        executor="local",
        runtime_profile={},
        resource_limits={},
    )
    job.status = "running"
    await session.commit()

    report = await SubagentCancellationService(session).cancel_tree(child.id)
    cancelled = await executions.require(child.id)
    assert cancelled.status == "cancelled"
    assert cancelled.fencing_token == old_fence + 1
    assert cancelled.cancellation_epoch == old_epoch + 1
    assert report.result_unknown_tool_call_ids == (uncertain.id,)
    assert report.terminated_sandbox_job_ids == (job.id,)
    assert report.immutable_effects[0]["output"] == {"remote_id": "immutable-1"}

    with pytest.raises(AgentExecutionStateError, match="Stale"):
        await executions.save_checkpoint(
            child.id,
            worker_id="old-worker",
            fencing_token=old_fence,
            cancellation_epoch=old_epoch,
            expected_state_version=old_version,
            checkpoint={"resume_safe": True},
        )


async def test_run_cancellation_epoch_prevents_new_child_claim(session):
    run, _, child = await _child(session)
    await RunUnitOfWork(session).cancel_run(run.id)
    cancelled_run = await RunUnitOfWork(session).require_run(run.id)
    cancelled_child = await AgentExecutionRepository(session).require(child.id)

    assert cancelled_run.cancellation_epoch == 1
    assert cancelled_child.cancellation_epoch == 1
    with pytest.raises(AgentExecutionStateError, match="cancelled Run"):
        await AgentExecutionRepository(session).claim(
            child.id,
            worker_id="late-worker",
            expected_state_version=cancelled_child.state_version,
            expected_cancellation_epoch=cancelled_child.cancellation_epoch,
        )


async def test_child_approval_is_exactly_bound_without_blocking_run(session):
    run, root, child = await _child(session)
    parent_identity = AgentIdentityRecord(
        task_id=run.task_id,
        run_id=run.id,
        identity_type="main_agent",
        principal="parent",
    )
    child_identity = AgentIdentityRecord(
        task_id=run.task_id,
        run_id=run.id,
        parent_identity_id=parent_identity.id,
        identity_type="subagent",
        principal="child",
    )
    session.add_all([parent_identity, child_identity])
    await session.flush()
    delegation = AgentDelegationRecord(
        parent_identity_id=parent_identity.id,
        child_identity_id=child_identity.id,
        delegated_scope={"actions": ["external_write"]},
    )
    session.add(delegation)
    await session.flush()
    child.identity_id = child_identity.id
    child.delegation_id = delegation.id
    child.catalog_snapshot = {"tool_digest": "sha256:catalog", "skill_digest": "sha256:skills"}
    child.status = "waiting_approval"
    root.status = "running"
    run.status = "executing"
    runs = RunUnitOfWork(session)
    turn = await runs.create_agent_turn(
        run.id,
        1,
        "tool",
        "needs approval",
        agent_execution_id=child.id,
    )
    call = await runs.start_tool_call(
        run.id,
        None,
        "external.write",
        "1",
        {"target": "record-1"},
        "external_write",
        "external_write",
        status="awaiting_approval",
        agent_execution_id=child.id,
    )
    request = await runs.create_approval_request(
        ApprovalRequestCreate(
            run_id=run.id,
            turn_id=turn.id,
            tool_call_id=call.id,
            tool_name="external.write",
            tool_version="1",
            frozen_input={"target": "record-1"},
            input_hash="sha256:input",
            frozen_effect_plan={"effects": [{"kind": "external_write"}]},
            effect_plan_hash="sha256:effect",
            preview="write record-1",
            permission="external_write",
            impact="high",
            similar_matcher=None,
            agent_execution_id=child.id,
            requester_identity_id=child_identity.id,
            delegation_id=delegation.id,
            catalog_digest="sha256:catalog",
            continuation_token="approval-token",
            grant_scope={"parent_identity_id": parent_identity.id},
        )
    )

    assert run.status == "executing"
    assert root.status == "running"
    assert request.grant_scope["input_hash"] == "sha256:input"
    await runs.decide_approval(
        run.id,
        request.id,
        "allow_once",
        continuation_token="approval-token",
        reviewer_identity={"id": "human-1", "identity_type": "human"},
    )
    assert (await AgentExecutionRepository(session).require(child.id)).status == "queued"
    assert (await RunUnitOfWork(session).require_run(run.id)).status == "executing"


async def test_recovery_resumes_safe_checkpoint_and_fails_closed_on_drift(session):
    run, root, safe = await _child(session, "safe")
    executions = AgentExecutionRepository(session)
    safe.catalog_snapshot = {"tool_digest": "tools-v1", "skill_digest": "skills-v1"}
    safe = await executions.claim(
        safe.id,
        worker_id="stale-safe",
        expected_state_version=safe.state_version,
    )
    safe.checkpoint = {
        "schema_version": 1,
        "runtime_version": "astra-subagent-v1",
        "resume_safe": True,
        "tool_catalog_digest": "tools-v1",
        "skill_catalog_digest": "skills-v1",
    }
    safe.heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
    drifted = await executions.create_child(
        contract=_contract(run, root, "drifted"),
        catalog_snapshot={"tool_digest": "tools-v2", "skill_digest": "skills-v1"},
    )
    drifted = await executions.claim(
        drifted.id,
        worker_id="stale-drifted",
        expected_state_version=drifted.state_version,
    )
    drifted.checkpoint = {
        "schema_version": 1,
        "runtime_version": "astra-subagent-v1",
        "resume_safe": True,
        "tool_catalog_digest": "tools-v1",
        "skill_catalog_digest": "skills-v1",
    }
    drifted.heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
    await session.flush()

    result = await SubagentExecutionRecovery(session, stale_seconds=1).scan(run.id)
    await session.commit()
    assert result.resumable_execution_ids == (safe.id,)
    assert result.incompatible_execution_ids == (drifted.id,)
    assert (await executions.require(safe.id)).status == "queued"
    failed = await executions.require(drifted.id)
    assert failed.status == "failed"
    assert failed.error["reason"] == "tool_catalog_version_drift"


async def test_recovery_never_replays_unknown_non_idempotent_call(session):
    run, _, child = await _child(session)
    executions = AgentExecutionRepository(session)
    child = await executions.claim(
        child.id,
        worker_id="stale-worker",
        expected_state_version=child.state_version,
    )
    child.heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
    call = await RunUnitOfWork(session).start_tool_call(
        run.id,
        None,
        "external.write",
        "1",
        {},
        "external_write",
        "external_write",
        agent_execution_id=child.id,
    )
    child.heartbeat_at = datetime.now(UTC) - timedelta(minutes=5)
    await session.flush()

    result = await SubagentExecutionRecovery(session, stale_seconds=1).scan(run.id)
    await session.commit()
    assert result.unknown_execution_ids == (child.id,)
    assert (await executions.require(child.id)).status == "waiting_resource"
    assert (await session.get(ToolCallRecord, call.id)).status == "result_unknown"


async def test_run_projection_exposes_sanitized_nested_agent_tree(session):
    run, root, child = await _child(session)
    join = AgentJoinRecord(
        run_id=run.id,
        parent_execution_id=root.id,
        join_key="final-review",
        group_id="reviewers",
        policy="required",
        child_execution_ids=[child.id],
        required_execution_ids=[child.id],
        status="waiting",
    )
    session.add(join)
    child.context_manifest = {
        "execution_context": {
            "effective_scope": {"actions": ["network_read"]},
            "hidden_reasoning": "must never be projected",
        }
    }
    child.catalog_snapshot = {"tools": [{"name": "catalog_search", "input_schema": {"secret": "hidden"}}]}
    child.result = {
        "summary": "bounded result",
        "artifacts": [{"id": "artifact-1"}],
        "open_issues": [],
        "scratchpad": "private notes",
    }
    child.status = "completed"
    child.phase = "terminal"
    await session.commit()

    loaded = await RunUnitOfWork(session).get_run(run.id)
    assert loaded is not None
    payload = run_payload(loaded)
    projected_root = payload["agent_executions"][0]
    projected_child = projected_root["children"][0]
    assert projected_root["id"] == root.id
    assert projected_child["id"] == child.id
    assert projected_child["permissions"] == ["network_read"]
    assert projected_child["capabilities"] == ["catalog_search"]
    assert projected_child["result_summary"] == "bounded result"
    assert payload["subagent_summary"]["completed"] == 1
    assert payload["agent_joins"][0]["join_key"] == "final-review"
    assert payload["agent_joins"][0]["status"] == "waiting"
    assert "hidden_reasoning" not in str(payload["agent_executions"])
    assert "private notes" not in str(payload["agent_executions"])
