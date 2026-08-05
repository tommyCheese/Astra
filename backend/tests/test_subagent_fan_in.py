from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.agent_runtime.policies.completion import CompletionGate
from app.application.agent_runtime.policies.reasoning import (
    build_default_contract,
    resolve_run_profile,
)
from app.application.agent_runtime.services.completion_gate import (
    CompletionGateInput,
    CompletionGateStage,
)
from app.application.agent_runtime.services.progress import ExecutionProgress
from app.application.subagents.fan_in import (
    SubagentJoinService,
    SubagentResultValidationError,
    SubagentResultValidator,
    merge_subagent_results,
    retry_subagent,
)
from app.application.subagents.governance import DelegationContractService
from app.application.subagents.supervisor import SubagentSupervisor
from app.common.core.config import Settings
from app.common.schemas.agent.execution_state import AgentState
from app.common.schemas.agent.run_policy import (
    EffectiveSubagentPolicy,
    RequestedReasoningPolicy,
    SubagentBudgetPolicy,
)
from app.common.schemas.agent.run_result import ValidationOutcome, VerificationReport
from app.common.schemas.agent.types import AnswerMode, PlanExecution, TerminalState
from app.common.schemas.permissions import PermissionPolicySet, PermissionRule
from app.common.schemas.subagents import (
    DelegationContract,
    DelegationRequest,
    DelegationScope,
    SubagentArtifactReference,
    SubagentEvidenceReference,
    SubagentExecutionStatus,
    SubagentJoinPolicy,
    SubagentResult,
)
from app.infrastructure.db.models.runs import EvidenceRecord
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.plans import PlanRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import ToolRegistry

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _contract(root, request_id: str) -> DelegationContract:
    return DelegationContract(
        contract_id=f"dc-{request_id}",
        contract_hash=f"sha256:{request_id}",
        task_id=root.task_id,
        run_id=root.run_id,
        parent_execution_id=root.id,
        depth=1,
        request=DelegationRequest(
            request_id=request_id,
            objective=f"Research {request_id}",
            success_criteria=["Return a verified finding"],
            scope=DelegationScope(included=[f"topic:{request_id}"]),
            output_schema={
                "type": "object",
                "properties": {"finding": {"type": "string"}},
                "required": ["finding"],
            },
            dedupe_key=request_id,
        ),
        created_at=NOW,
    )


async def _child(session, root, request_id: str):
    child = await AgentExecutionRepository(session).create_child(
        contract=_contract(root, request_id)
    )
    await session.commit()
    return child


async def _complete(
    session,
    child,
    *,
    finding: str,
    artifact: bool = False,
    evidence: bool = True,
    warnings: list[str] | None = None,
):
    artifact_refs = []
    if artifact:
        record = await RunUnitOfWork(session).create_artifact(
            child.run_id,
            "child_report",
            agent_execution_id=child.id,
            path=f".astra/subagents/{child.id}/staging/report.md",
            content_ref="object://report",
            mime_type="text/markdown",
            size_bytes=100,
            checksum="sha256:report",
            security_status="verified",
            provenance={"source_agent_execution_id": child.id},
        )
        artifact_refs.append(
            SubagentArtifactReference(
                id=record.id,
                uri=f"artifact://{record.id}",
                mime_type=record.mime_type,
                content_hash=record.checksum,
            )
        )
    evidence_refs = []
    if evidence:
        record = EvidenceRecord(
            run_id=child.run_id,
            agent_execution_id=child.id,
            evidence_id=f"ev-{child.request_id}",
            evidence_key=f"child:{child.request_id}",
            kind="claim",
            payload_digest=f"digest-{child.request_id}",
            fragment={"payload": {"finding": finding}},
        )
        session.add(record)
        await session.flush()
        evidence_refs.append(SubagentEvidenceReference(id=record.id))
    status = (
        SubagentExecutionStatus.completed_with_warnings
        if warnings
        else SubagentExecutionStatus.completed
    )
    result = SubagentResult(
        status=status,
        summary=finding,
        outputs={"finding": finding},
        artifacts=artifact_refs,
        evidence_refs=evidence_refs,
        claims=[
            {
                "key": "primary",
                "text": finding,
                "material": True,
                "evidence_refs": [item.id for item in evidence_refs],
            }
        ]
        if evidence_refs
        else [],
        open_issues=warnings or [],
        completion={"state": status.value},
        provenance={
            "agent_execution_id": child.id,
            "contract_hash": child.contract["contract_hash"],
        },
    )
    child.status = status.value
    child.phase = "terminal"
    child.result = result.model_dump(mode="json")
    await session.commit()
    return result


async def test_result_validator_checks_schema_lineage_artifacts_evidence_and_completion(session):
    run = await RunUnitOfWork(session).create_task_run("Validate children", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    valid = await _child(session, root, "valid")
    await _complete(session, valid, finding="verified", artifact=True)
    normalized = await SubagentResultValidator(session).validate(valid.id)
    assert normalized.result.outputs == {"finding": "verified"}
    assert len(normalized.artifact_ids) == 1
    assert len(normalized.evidence_ids) == 1

    missing = await _child(session, root, "missing")
    missing.status = "completed"
    missing.phase = "terminal"
    missing.result = SubagentResult(
        status="completed",
        outputs={"finding": "unsupported"},
        artifacts=[
            SubagentArtifactReference(id="missing", uri="artifact://missing")
        ],
        completion={"state": "completed"},
    ).model_dump(mode="json")
    await session.commit()
    with pytest.raises(SubagentResultValidationError, match="missing Artifact"):
        await SubagentResultValidator(session).validate(missing.id)

    unsupported = await _child(session, root, "unsupported")
    unsupported.status = "completed"
    unsupported.phase = "terminal"
    unsupported.result = SubagentResult(
        status="completed",
        outputs={"finding": "claim"},
        claims=[{"text": "claim", "material": True}],
        completion={"state": "completed"},
    ).model_dump(mode="json")
    await session.commit()
    with pytest.raises(SubagentResultValidationError, match="missing Evidence"):
        await SubagentResultValidator(session).validate(unsupported.id)


async def test_required_optional_and_first_success_join_semantics(session):
    run = await RunUnitOfWork(session).create_task_run("Join children", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    first = await _child(session, root, "first")
    second = await _child(session, root, "second")
    optional = await _child(session, root, "optional")
    await _complete(session, first, finding="first")
    joins = SubagentJoinService(session)
    required = await joins.create(
        parent_execution_id=root.id,
        join_key="required",
        child_execution_ids=[first.id, second.id],
        policy=SubagentJoinPolicy.required,
    )
    assert (await joins.evaluate(required.id)).status == "waiting"
    second.status = "failed"
    second.phase = "terminal"
    second.result = SubagentResult(
        status="failed", summary="failed", completion={"state": "failed"}
    ).model_dump(mode="json")
    optional.status = "failed"
    optional.phase = "terminal"
    optional.result = SubagentResult(
        status="failed", summary="optional failed", completion={"state": "failed"}
    ).model_dump(mode="json")
    await session.commit()
    required_result = await joins.evaluate(required.id)
    assert required_result.status == "blocked"
    optional_join = await joins.create(
        parent_execution_id=root.id,
        join_key="optional",
        child_execution_ids=[first.id, optional.id],
        policy=SubagentJoinPolicy.optional,
        required_execution_ids=[first.id],
        optional_execution_ids=[optional.id],
    )
    optional_result = await joins.evaluate(optional_join.id)
    assert optional_result.status == "ready"
    assert optional.id in optional_result.failed_ids

    racer = await _child(session, root, "racer")
    first_success = await joins.create(
        parent_execution_id=root.id,
        join_key="first-success",
        child_execution_ids=[first.id, racer.id],
        policy=SubagentJoinPolicy.first_success,
    )
    race_result = await joins.evaluate(first_success.id)
    assert race_result.status == "ready"
    assert racer.id in race_result.loser_ids
    cancelled, unsafe = await joins.cancel_safe_first_success_losers(race_result)
    assert cancelled == (racer.id,)
    assert unsafe == ()
    await session.refresh(racer)
    assert racer.status == "cancelled"


async def test_result_merger_deduplicates_and_preserves_conflicts_and_warnings(session):
    run = await RunUnitOfWork(session).create_task_run("Merge children", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    first = await _child(session, root, "merge-one")
    second = await _child(session, root, "merge-two")
    await _complete(session, first, finding="A", warnings=["limited coverage"])
    await _complete(session, second, finding="B")
    validator = SubagentResultValidator(session)
    merged = merge_subagent_results(
        [await validator.validate(first.id), await validator.validate(second.id)]
    )
    assert merged.conflicts[0]["kind"] == "fact_conflict"
    assert merged.facts[0]["value"] == "A"
    assert merged.warnings == ("limited coverage",)
    assert set(merged.source_execution_ids) == {first.id, second.id}


def _supervisor(session, run, root) -> SubagentSupervisor:
    return SubagentSupervisor(
        settings=Settings(model_provider="mock"),
        session=session,
        session_factory=async_sessionmaker(
            session.bind,
            expire_on_commit=False,
            class_=type(session),
        ),
        run_id=run.id,
        parent_execution_id=root.id,
        parent_identity_id=root.identity_id or "test-root-identity",
        policy=EffectiveSubagentPolicy(
            enabled=True,
            read_only=True,
            rollout_cohort="trusted_read_only",
        ),
        tool_registry=ToolRegistry(),
        model_client_factory=MockModelClient,
    )


async def _merged_payload(session, join, child) -> dict:
    validated = await SubagentResultValidator(session).validate(child.id)
    merged = merge_subagent_results([validated])
    return {
        **{
            key: list(value) if isinstance(value, tuple) else value
            for key, value in merged.__dict__.items()
        },
        "group_id": join.group_id,
        "join_id": join.id,
        "unsafe_loser_execution_ids": [],
    }


@pytest.mark.parametrize(
    "crash_point",
    [
        "before_merge",
        "during_promotion",
        "before_consumed_commit",
        "after_consumed_commit_before_projection",
    ],
)
async def test_join_crash_recovery_projects_parent_result_exactly_once(session, crash_point):
    run = await RunUnitOfWork(session).create_task_run("Join crash recovery", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    child = await _child(session, root, f"crash-{crash_point}")
    await _complete(session, child, finding="durable finding")
    joins = SubagentJoinService(session)
    join = await joins.create(
        parent_execution_id=root.id,
        join_key=f"join:{crash_point}",
        group_id=f"group:{crash_point}",
        child_execution_ids=[child.id],
        policy=SubagentJoinPolicy.required,
    )
    await joins.evaluate(join.id)
    await session.refresh(join)
    assert join.status == "ready"

    if crash_point != "before_merge":
        join = await joins.begin_merge(join.id, expected_version=join.state_version)
        if crash_point == "during_promotion":
            await session.commit()
        else:
            payload = await _merged_payload(session, join, child)
            await joins.mark_consumed(
                join.id,
                expected_version=join.state_version,
                parent_state_version=run.state_version,
                result=payload,
            )
            if crash_point == "before_consumed_commit":
                await session.rollback()
                await session.refresh(run)
                await session.refresh(root)
                await session.refresh(join)
            else:
                await session.commit()

    recovered = _supervisor(session, run, root)
    observations = await recovered.reconcile(parent_state_version=run.state_version)
    assert len(observations) == 1
    assert observations[0]["data"]["join_id"] == join.id
    assert observations[0]["data"]["facts"][0]["value"] == "durable finding"
    assert await recovered.reconcile(parent_state_version=run.state_version) == []

    await session.refresh(run)
    run.agent_state = {"observations": observations}
    await session.commit()
    after_checkpoint_recovery = _supervisor(session, run, root)
    assert await after_checkpoint_recovery.reconcile(parent_state_version=run.state_version) == []


def test_root_completion_gate_waits_for_descendants_and_required_joins():
    state = AgentState(task_contract=build_default_contract("Complete with children"))
    gate = CompletionGate()
    validation = [ValidationOutcome(validator="test", passed=True)]
    waiting = gate.evaluate(
        state,
        validation_outcomes=validation,
        descendant_executions=[{"id": "child-1", "status": "running"}],
        required_joins=[{"id": "join-1", "status": "waiting"}],
    )
    blocked = gate.evaluate(
        state,
        validation_outcomes=validation,
        descendant_executions=[{"id": "child-1", "status": "failed"}],
        required_joins=[{"id": "join-1", "status": "blocked"}],
    )
    assert waiting.state.value == "continue"
    assert "agent-execution:child-1" in waiting.unmet_criteria
    assert blocked.state.value == "blocked"


async def test_persisted_root_completion_barrier_waits_through_join_consumption(session):
    settings = Settings(model_provider="mock")
    profile = resolve_run_profile(
        AnswerMode.trusted,
        RequestedReasoningPolicy(execution_mode="auto_approval"),
        plan_execution=PlanExecution.auto,
    )
    repository = RunUnitOfWork(session)
    run = await repository.create_task_run(
        "Complete only after child merge",
        settings.model_policy,
        reasoning_policy=profile.reasoning_policy.model_dump(mode="json"),
        answer_mode=profile.answer_mode.value,
        execution_profile=profile.model_dump(mode="json"),
    )
    contract = build_default_contract(run.task.description)
    state = AgentState(task_contract=contract)
    await repository.initialize_reasoning_state(
        run.id,
        task_contract=contract.model_dump(mode="json"),
        plan_graph={},
        agent_state=state.model_dump(mode="json"),
    )
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    child = await _child(session, root, "completion-barrier")
    joins = SubagentJoinService(session)
    join = await joins.create(
        parent_execution_id=root.id,
        join_key="join:completion-barrier",
        group_id="group:completion-barrier",
        child_execution_ids=[child.id],
        policy=SubagentJoinPolicy.required,
    )
    stage = CompletionGateStage(repository, PlanRepository(session), CompletionGate())
    progress = ExecutionProgress(active_plan=None)
    verification = VerificationReport(
        status="passed",
        validation_outcomes=[ValidationOutcome(validator="task_adapter", passed=True)],
    )

    while_child_runs = await stage.evaluate(
        CompletionGateInput(run.id, profile, progress, None), verification
    )
    assert while_child_runs.state == TerminalState.continue_run
    assert f"agent-execution:{child.id}" in while_child_runs.unmet_criteria

    await _complete(session, child, finding="merged before root completion")
    await joins.evaluate(join.id)
    await session.refresh(join)
    assert join.status == "ready"
    while_merge_is_unconsumed = await stage.evaluate(
        CompletionGateInput(run.id, profile, progress, None), verification
    )
    assert while_merge_is_unconsumed.state == TerminalState.continue_run
    assert f"agent-join:{join.id}" in while_merge_is_unconsumed.unmet_criteria

    join = await joins.begin_merge(join.id, expected_version=join.state_version)
    current = await repository.require_run_core(run.id)
    await joins.mark_consumed(
        join.id,
        expected_version=join.state_version,
        parent_state_version=current.state_version,
        result=await _merged_payload(session, join, child),
    )
    await session.commit()
    after_consumption = await stage.evaluate(
        CompletionGateInput(run.id, profile, progress, None), verification
    )
    assert after_consumption.state == TerminalState.completed


async def test_safe_retry_preserves_failed_attempt_and_creates_new_identity(session):
    run = await RunUnitOfWork(session).create_task_run("Retry child", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    permissions = PermissionRepository(session)
    parent = await permissions.create_identity(
        identity_type="main_agent",
        principal="parent",
        run_id=run.id,
        attributes={"permission_scope": {"actions": ["*"], "resources": ["*"]}},
    )
    root.identity_id = parent.id
    await permissions.freeze_tool_catalog(run.id, catalog=[], digest="sha256:empty")
    failed = await _child(session, root, "retry")
    failed.status = "failed"
    failed.phase = "terminal"
    failed.result = {"status": "failed", "summary": "transient"}
    failed.checkpoint = {"attempt": 1}
    await session.commit()
    policy = EffectiveSubagentPolicy(
        enabled=True,
        read_only=True,
        budgets=SubagentBudgetPolicy(
            max_children_total=3,
            max_children_per_parent=3,
            max_parallel_children=1,
            max_depth=1,
            max_parent_round_trips=0,
            max_wall_time_seconds=300,
            max_tokens=8_000,
            max_model_calls=4,
            max_tool_calls=8,
            max_cost_usd=0.5,
        ),
    )
    service = DelegationContractService(
        session,
        policy=policy,
        permission_policies=PermissionPolicySet(
            version="retry",
            rules=[
                PermissionRule(
                    id="allow",
                    source="test",
                    tier="run",
                    decision="allow",
                    actions=["delegation_create"],
                    resources=["identity://*"],
                    reason_code="retry_allowed",
                )
            ],
        ),
    )
    retry = await retry_subagent(service, failed.id, retry_safe=True)
    assert retry.id != failed.id
    assert retry.identity_id is not None
    assert retry.checkpoint["retry_of_execution_id"] == failed.id
    assert retry.checkpoint["attempt"] == 2
    await session.refresh(failed)
    assert failed.status == "failed"
