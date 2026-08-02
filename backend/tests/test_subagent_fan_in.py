from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.db.models import EvidenceRecord
from app.repositories.agent_executions import AgentExecutionRepository
from app.repositories.permissions import PermissionRepository
from app.repositories.runs import RunRepository
from app.runner.reasoning import CompletionGate, build_default_contract
from app.schemas.agent import (
    AgentState,
    EffectiveSubagentPolicy,
    SubagentBudgetPolicy,
    ValidationOutcome,
)
from app.schemas.permissions import PermissionPolicySet, PermissionRule
from app.schemas.subagents import (
    DelegationContract,
    DelegationRequest,
    DelegationScope,
    SubagentArtifactReference,
    SubagentEvidenceReference,
    SubagentExecutionStatus,
    SubagentJoinPolicy,
    SubagentResult,
)
from app.subagents.fan_in import (
    SubagentFailureManager,
    SubagentJoinService,
    SubagentResultMerger,
    SubagentResultValidationError,
    SubagentResultValidator,
)
from app.subagents.governance import DelegationContractService

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
        record = await RunRepository(session).create_artifact(
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
    run = await RunRepository(session).create_task_run("Validate children", {})
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
    run = await RunRepository(session).create_task_run("Join children", {})
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
    run = await RunRepository(session).create_task_run("Merge children", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    first = await _child(session, root, "merge-one")
    second = await _child(session, root, "merge-two")
    await _complete(session, first, finding="A", warnings=["limited coverage"])
    await _complete(session, second, finding="B")
    validator = SubagentResultValidator(session)
    merged = SubagentResultMerger().merge(
        [await validator.validate(first.id), await validator.validate(second.id)]
    )
    assert merged.conflicts[0]["kind"] == "fact_conflict"
    assert merged.facts[0]["value"] == "A"
    assert merged.warnings == ("limited coverage",)
    assert set(merged.source_execution_ids) == {first.id, second.id}


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


async def test_safe_retry_preserves_failed_attempt_and_creates_new_identity(session):
    run = await RunRepository(session).create_task_run("Retry child", {})
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
    retry = await SubagentFailureManager(service).retry(failed.id, retry_safe=True)
    assert retry.id != failed.id
    assert retry.identity_id is not None
    assert retry.checkpoint["retry_of_execution_id"] == failed.id
    assert retry.checkpoint["attempt"] == 2
    await session.refresh(failed)
    assert failed.status == "failed"
