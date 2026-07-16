from datetime import timedelta

from app.db.models import ApprovalGrantRecord, utc_now
from app.permissions.engine import PermissionEngine
from app.repositories.runs import RunRepository
from app.schemas.permissions import (
    PermissionConditions,
    PermissionDecisionKind,
    PermissionPolicySet,
    PermissionRequest,
    PermissionRule,
    PermissionSubject,
)


def permission_request(
    *,
    action: str = "workspace.file.write",
    resource: str = "task://task-1/workspace/reports/summary.md",
) -> PermissionRequest:
    return PermissionRequest(
        subject=PermissionSubject(
            agent_id="agent:main",
            task_id="task-1",
            run_id="run-1",
        ),
        action=action,
        resource=resource,
        conditions=PermissionConditions(
            tool_name="file_write",
            tool_version="1",
            analyzer_version="2",
        ),
        context={"effect_kinds": ["workspace_write"]},
    )


def rule(
    rule_id: str,
    tier: str,
    decision: str,
    *,
    resources: list[str] | None = None,
) -> PermissionRule:
    return PermissionRule(
        id=rule_id,
        source=f"{tier}:test",
        tier=tier,
        decision=decision,
        actions=["workspace.file.*"],
        resources=resources or ["task://*/workspace/**"],
        reason_code=f"{rule_id}_reason",
    )


def test_permission_engine_applies_deny_then_ask_then_allow_across_tiers():
    engine = PermissionEngine()
    request = permission_request()

    denied = engine.evaluate(
        request,
        PermissionPolicySet(
            version="1",
            rules=[
                rule("managed-allow", "managed", "allow"),
                rule("task-deny", "task", "deny"),
            ],
        ),
    )
    asked = engine.evaluate(
        request,
        PermissionPolicySet(
            version="1",
            rules=[
                rule("managed-ask", "managed", "ask"),
                rule("run-allow", "run", "allow"),
            ],
        ),
    )
    allowed = engine.evaluate(
        request,
        PermissionPolicySet(
            version="1",
            rules=[rule("platform-allow", "platform", "allow")],
        ),
    )

    assert denied.decision == PermissionDecisionKind.deny
    assert denied.explanation.reason_code == "policy_denied"
    assert asked.decision == PermissionDecisionKind.ask
    assert asked.explanation.matched_policies[0].tier == "managed"
    assert allowed.decision == PermissionDecisionKind.allow


def test_protected_resources_cannot_be_modified_by_lower_trust_allow():
    decision = PermissionEngine().evaluate(
        permission_request(
            action="permission.policy.write",
            resource="astra://permission/platform",
        ),
        PermissionPolicySet(
            version="1",
            rules=[
                PermissionRule(
                    id="task-allow",
                    source="task:test",
                    tier="task",
                    decision="allow",
                    actions=["*"],
                    resources=["*"],
                    reason_code="task_requested",
                )
            ],
        ),
    )

    assert decision.decision == PermissionDecisionKind.deny
    assert decision.explanation.reason_code == "protected_resource"


def test_scoped_lease_requires_matching_subject_effect_resource_and_invocation():
    request = permission_request()
    grant = ApprovalGrantRecord(
        id="grant-1",
        run_id="run-1",
        task_id="task-1",
        scope="run",
        subject={"agent_id": "agent:main", "run_id": "run-1"},
        tool_name="file_write",
        tool_version="1",
        matcher={},
        effect_kinds=["workspace_write"],
        resource_matcher={"glob": "task://task-1/workspace/reports/**"},
        invocation_constraints={
            "tool_name": "file_write",
            "tool_version": "1",
            "analyzer_version": "2",
        },
        source_approval_id="approval-1",
        status="active",
        use_count=0,
        max_uses=2,
        expires_at=utc_now() + timedelta(minutes=5),
    )
    engine = PermissionEngine()

    allowed = engine.evaluate(
        request,
        PermissionPolicySet(version="1"),
        [grant],
    )
    request.conditions.tool_version = "2"
    asked = engine.evaluate(
        request,
        PermissionPolicySet(version="1"),
        [grant],
    )

    assert allowed.decision == PermissionDecisionKind.allow
    assert allowed.explanation.reason_code == "permission_lease"
    assert asked.decision == PermissionDecisionKind.ask
    assert "grant_invocation_mismatch" in asked.explanation.trace[0]


async def test_permission_lease_consumption_revocation_and_integrity_invalidation(session):
    repository = RunRepository(session)
    run = await repository.create_task_run("Lease lifecycle", {})
    turn = await repository.create_agent_turn(
        run.id,
        1,
        "call_tool",
        "Write report",
        selected_tool="file_write",
        phase="prepared",
    )
    call = await repository.start_tool_call(
        run.id,
        None,
        "file_write",
        "1",
        {"path": "reports/summary.md"},
        "workspace_write",
        "persistent_side_effect",
        status="awaiting_approval",
    )
    approval = await repository.create_approval_request(
        run_id=run.id,
        turn_id=turn.id,
        tool_call_id=call.id,
        tool_name="file_write",
        tool_version="1",
        frozen_input={"path": "reports/summary.md"},
        input_hash="sha256:input",
        preview="Write report",
        permission="workspace_write",
        impact="persistent_side_effect",
        similar_matcher={
            "kind": "resource_glob",
            "resource_matcher": {"glob": "task://*/workspace/reports/**"},
            "tool_version": "1",
            "analyzer_digest": "sha256:one",
        },
    )
    waiting = await repository.set_waiting_state(
        run.id,
        {"kind": "tool_approval", "approval_id": approval.id, "tool_call_id": call.id},
    )
    await repository.decide_approval(
        run.id,
        approval.id,
        "allow_similar",
        continuation_token=waiting.waiting_state["continuation_token"],
    )
    grant = (await repository.list_approval_grants(run.id, "file_write", "1"))[0]
    grant.max_uses = 2
    grant.expires_at = utc_now() + timedelta(minutes=5)
    await session.commit()

    consumed = await repository.consume_approval_grant(grant.id)
    assert consumed.use_count == 1
    invalidated = await repository.invalidate_approval_grants_for_tool_identity(
        run.id,
        tool_name="file_write",
        tool_version="1",
        analyzer_digest="sha256:changed",
    )
    assert invalidated[0].status == "invalidated"

    second = ApprovalGrantRecord(
        run_id=run.id,
        task_id=run.task_id,
        scope="run",
        subject={},
        tool_name="file_write",
        tool_version="1",
        matcher={},
        source_approval_id=approval.id,
        status="active",
    )
    session.add(second)
    await session.commit()
    revoked = await repository.revoke_approval_grant(second.id)
    assert revoked.status == "revoked"
    assert revoked.revoked_at is not None
