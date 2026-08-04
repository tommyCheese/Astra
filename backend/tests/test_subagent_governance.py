from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models.skills import RunSkillSnapshotRecord
from app.repositories.agent_executions import AgentExecutionRepository
from app.repositories.permissions import PermissionRepository
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.agent.run_policy import EffectiveSubagentPolicy, SubagentBudgetPolicy
from app.schemas.permissions import (
    ActionEffectPlan,
    EffectItem,
    PermissionPolicySet,
    PermissionRule,
)
from app.schemas.subagents import (
    DelegatedExecutionContext,
    DelegationRejectionCode,
    DelegationRequest,
    DelegationScope,
    EffectiveDelegationScope,
    SubagentBudgetEnvelope,
)
from app.subagents.governance import (
    ChildInvocationAuthorizer,
    DelegationAuthorizationError,
    DelegationContractService,
    DelegationScopeAttenuator,
    FrozenChildCatalog,
)


def _policy(*, read_only: bool = True, max_depth: int = 1) -> EffectiveSubagentPolicy:
    return EffectiveSubagentPolicy(
        enabled=True,
        read_only=read_only,
        budgets=SubagentBudgetPolicy(
            max_children_total=4,
            max_children_per_parent=2,
            max_parallel_children=1,
            max_depth=max_depth,
            max_parent_round_trips=1,
            max_wall_time_seconds=120,
            max_tokens=10_000,
            max_model_calls=6,
            max_tool_calls=10,
            max_cost_usd=1,
        ),
    )


def _allow_policy(*actions: str) -> PermissionPolicySet:
    return PermissionPolicySet(
        version="test",
        rules=[
            PermissionRule(
                id=f"allow-{action}",
                source="test",
                tier="run",
                decision="allow",
                actions=[action],
                resources=["*"],
                reason_code=f"test_allow_{action}",
            )
            for action in actions
        ],
    )


def _request(
    *,
    request_id: str = "req-1",
    dedupe_key: str = "research:one",
    included: str = "topic:one",
    skills: list[str] | None = None,
) -> DelegationRequest:
    return DelegationRequest(
        request_id=request_id,
        objective="Research one bounded topic",
        success_criteria=["Return a sourced finding"],
        scope=DelegationScope(included=[included]),
        output_schema={
            "type": "object",
            "properties": {"finding": {"type": "string"}},
            "required": ["finding"],
        },
        requested_tools=["web_search"],
        requested_skills=skills or [],
        resource_scope={
            "actions": ["network_read"],
            "resources": ["https://example.com/**"],
            "effect_kinds": ["network_read"],
            "tools": ["web_search"],
            "skills": skills or [],
            "data_labels": ["public"],
            "allowed_purposes": ["research"],
            "network_destinations": ["https://example.com/**"],
            "workspace_read_roots": ["inputs/**"],
            "max_tool_calls": 4,
            "max_runtime_seconds": 120,
        },
        budget=SubagentBudgetEnvelope(
            max_tokens=8_000,
            max_model_calls=4,
            max_tool_calls=4,
            max_wall_time_ms=120_000,
            max_cost_usd=0.5,
        ),
        dedupe_key=dedupe_key,
    )


async def _runtime(session, *, skills: bool = False):
    run = await RunUnitOfWork(session).create_task_run("Governed delegation", {})
    executions = AgentExecutionRepository(session)
    root = await executions.root_for_run(run.id)
    assert root is not None
    permissions = PermissionRepository(session)
    parent_scope = {
        "actions": ["network_read", "credential_use"],
        "resources": ["https://example.com/**"],
        "effect_kinds": ["network_read"],
        "tools": ["web_search"],
        "skills": ["managed:research"] if skills else [],
        "credential_scopes": ["records.read"],
        "data_labels": ["public"],
        "allowed_purposes": ["research"],
        "network_destinations": ["https://example.com/**"],
        "workspace_read_roots": ["inputs/**"],
        "workspace_write_roots": [],
        "max_tool_calls": 8,
        "max_runtime_seconds": 300,
    }
    parent = await permissions.create_identity(
        identity_type="main_agent",
        principal="astra.agent",
        run_id=run.id,
        attributes={"permission_scope": parent_scope},
    )
    root.identity_id = parent.id
    await permissions.freeze_tool_catalog(
        run.id,
        catalog=[
            {
                "name": "web_search",
                "version": "1",
                "permissions": ["network_read"],
                "side_effect_level": "none",
            },
            {
                "name": "file_write",
                "version": "1",
                "permissions": ["workspace_write"],
                "side_effect_level": "persistent",
            },
        ],
        digest="sha256:parent-tools",
    )
    if skills:
        session.add(
            RunSkillSnapshotRecord(
                run_id=run.id,
                catalog_digest="sha256:parent-skills",
                catalog=[
                    {
                        "qualified_identity": "managed:research",
                        "revision_id": "revision-1",
                        "digest": "sha256:skill",
                        "requested_tool_patterns": ["web_*"],
                    }
                ],
                answer_mode="trusted",
            )
        )
    await session.commit()
    return run, root, parent, parent_scope


async def test_contract_service_attenuates_identity_catalog_and_deduplicates(session):
    run, root, parent, parent_scope = await _runtime(session, skills=True)
    service = DelegationContractService(
        session,
        policy=_policy(),
        permission_policies=_allow_policy("delegation_create"),
        task_policy_scope=parent_scope,
    )

    child = await service.authorize_and_create(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        request=_request(skills=["managed:research"]),
    )
    child_identity = await session.get(type(parent), child.identity_id)

    assert child.run_id == run.id
    assert child.contract["contract_hash"].startswith("sha256:")
    assert child.catalog_snapshot["tools"][0]["name"] == "web_search"
    assert child.catalog_snapshot["skills"][0]["revision_id"] == "revision-1"
    assert child_identity.attributes["parent_secrets_inherited"] is False
    assert child_identity.attributes["permission_scope"]["actions"] == ["network_read"]
    with pytest.raises(DelegationAuthorizationError) as duplicate:
        await service.authorize_and_create(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=_request(request_id="req-2"),
        )
    assert duplicate.value.issue.code == DelegationRejectionCode.duplicate_request


async def test_scope_attenuation_rejects_amplification_writes_and_depth(session):
    _, root, parent, parent_scope = await _runtime(session)
    service = DelegationContractService(
        session,
        policy=_policy(),
        permission_policies=_allow_policy("delegation_create"),
        task_policy_scope=parent_scope,
    )
    request = _request()
    request.resource_scope["network_destinations"] = ["https://private.example/**"]
    with pytest.raises(DelegationAuthorizationError) as amplified:
        await service.authorize_and_create(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=request,
        )
    assert amplified.value.issue.code == DelegationRejectionCode.resource_not_delegated

    with pytest.raises(DelegationAuthorizationError) as write:
        DelegationScopeAttenuator.attenuate(
            requested={"actions": ["workspace_write"]},
            parent={"actions": ["*"]},
            task_policy=None,
            server_policy=_policy(read_only=True),
            execution_id="child",
        )
    assert write.value.issue.code == DelegationRejectionCode.resource_not_delegated

    child = await service.authorize_and_create(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        request=_request(),
    )
    with pytest.raises(DelegationAuthorizationError) as depth:
        await service.authorize_and_create(
            parent_execution_id=child.id,
            parent_identity_id=child.identity_id,
            request=_request(
                request_id="nested",
                dedupe_key="nested",
                included="topic:nested",
            ),
        )
    assert depth.value.issue.code == DelegationRejectionCode.excessive_depth


async def test_catalog_drift_skill_escape_and_sibling_overlap_fail_closed(session):
    _, root, parent, parent_scope = await _runtime(session, skills=True)
    service = DelegationContractService(
        session,
        policy=_policy(),
        permission_policies=_allow_policy("delegation_create"),
        task_policy_scope=parent_scope,
    )
    child = await service.authorize_and_create(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        request=_request(skills=["managed:research"]),
    )
    frozen = FrozenChildCatalog(
        tools=tuple(child.catalog_snapshot["tools"]),
        tool_digest=child.catalog_snapshot["tool_digest"],
        skills=tuple(child.catalog_snapshot["skills"]),
        skill_digest=child.catalog_snapshot["skill_digest"],
    )
    with pytest.raises(DelegationAuthorizationError) as drift:
        frozen.validate_resume({**frozen.model_dump(), "tool_digest": "sha256:changed"})
    assert drift.value.issue.code == DelegationRejectionCode.catalog_drift
    with pytest.raises(DelegationAuthorizationError):
        service.require_skill_activation(frozen, "managed:research", "revision-2")
    with pytest.raises(DelegationAuthorizationError) as overlap:
        await service.authorize_and_create(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=_request(
                request_id="req-other",
                dedupe_key="different",
                included="topic:one",
            ),
        )
    assert overlap.value.issue.code == DelegationRejectionCode.scope_overlap

    independent = await service.authorize_and_create(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        request=_request(
            request_id="independent-review",
            dedupe_key="independent-review",
            included="topic:one",
            skills=["managed:research"],
        ).model_copy(update={"relationship": "independent_review"}),
    )
    assert independent.contract["request"]["relationship"] == "independent_review"


async def test_skill_requiring_an_undelegated_tool_is_removed(session):
    run, root, parent, parent_scope = await _runtime(session, skills=True)
    snapshot = await session.scalar(
        select(RunSkillSnapshotRecord).where(RunSkillSnapshotRecord.run_id == run.id)
    )
    snapshot.catalog[0]["requested_tool_patterns"] = ["file_write"]
    await session.commit()
    service = DelegationContractService(
        session,
        policy=_policy(),
        permission_policies=_allow_policy("delegation_create"),
        task_policy_scope=parent_scope,
    )
    with pytest.raises(DelegationAuthorizationError) as rejected:
        await service.authorize_and_create(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=_request(skills=["managed:research"]),
        )
    assert rejected.value.issue.code == DelegationRejectionCode.capability_not_delegated


def _execution_context(frozen: FrozenChildCatalog) -> DelegatedExecutionContext:
    scope = EffectiveDelegationScope(
        actions=("network_read", "credential_use"),
        resources=("https://example.com/**",),
        effect_kinds=("network_read",),
        tools=("web_search",),
        credential_scopes=("records.read",),
        data_labels=("public",),
        allowed_purposes=("research",),
        network_destinations=("https://example.com/**",),
        workspace_read_roots=("inputs/**",),
        private_staging_root=".astra/subagents/exec-1/staging",
    )
    return DelegatedExecutionContext(
        task_id="task-1",
        run_id="run-1",
        agent_execution_id="exec-1",
        identity_id="child-1",
        parent_identity_id="parent-1",
        delegation_id="delegation-1",
        delegation_chain=("parent-1", "child-1"),
        purpose="research",
        effective_scope=scope,
        budget_envelope=SubagentBudgetEnvelope(),
        data_flow_state={
            "trust_sources": ["web:example"],
            "data_labels": ["public"],
            "allowed_destinations": ["https://example.com/**"],
            "prohibited_destinations": [],
        },
        workspace_scope={
            "read_roots": ["inputs/**"],
            "write_roots": [],
            "private_staging_root": scope.private_staging_root,
        },
        tool_catalog_digest=frozen.tool_digest,
        skill_catalog_digest=frozen.skill_digest,
    )


def test_child_invocation_binds_context_and_rejects_context_drop_or_escape():
    tools = (
        {
            "name": "web_search",
            "version": "1",
            "permissions": ["network_read"],
        },
    )
    frozen = FrozenChildCatalog(
        tools=tools,
        tool_digest="sha256:tools",
        skills=(),
        skill_digest="sha256:skills",
    )
    context = _execution_context(frozen)
    plan = ActionEffectPlan(
        tool_name="web_search",
        tool_version="1",
        summary="Read a public page",
        effects=[
            EffectItem(
                kind="network_read",
                resource="https://example.com/page",
                data_labels=["public"],
            )
        ],
        required_permissions=["network_read"],
        analyzer_version="1",
    )
    decision = ChildInvocationAuthorizer().authorize(
        context=context,
        frozen_catalog=frozen,
        tool_name="web_search",
        tool_version="1",
        effect_plan=plan,
        effect_plan_hash="sha256:effect",
        tool_input={"query": "test"},
        declared_permissions=["network_read"],
        execution_mode="request_approval",
        policies=_allow_policy("network_read"),
    )
    assert decision.decision.decision.value == "allow"
    assert decision.requests[0].subject.agent_execution_id == "exec-1"

    drifted = context.model_copy(update={"tool_catalog_digest": "sha256:changed"})
    with pytest.raises(DelegationAuthorizationError) as drift:
        ChildInvocationAuthorizer().authorize(
            context=drifted,
            frozen_catalog=frozen,
            tool_name="web_search",
            tool_version="1",
            effect_plan=plan,
            effect_plan_hash="sha256:effect",
            tool_input={},
            declared_permissions=["network_read"],
            execution_mode="request_approval",
        )
    assert drift.value.issue.code == DelegationRejectionCode.catalog_drift

    escaped = plan.model_copy(
        update={
            "effects": [
                EffectItem(kind="network_read", resource="https://evil.example/page")
            ]
        }
    )
    with pytest.raises(DelegationAuthorizationError):
        ChildInvocationAuthorizer().authorize(
            context=context,
            frozen_catalog=frozen,
            tool_name="web_search",
            tool_version="1",
            effect_plan=escaped,
            effect_plan_hash="sha256:escaped",
            tool_input={},
            declared_permissions=["network_read"],
            execution_mode="request_approval",
        )

    with pytest.raises(DelegationAuthorizationError) as self_approval:
        ChildInvocationAuthorizer.validate_reviewer(
            reviewer_identity_id="child-1", executor_context=context
        )
    assert self_approval.value.issue.code == DelegationRejectionCode.identity_overlap


def test_child_invocation_preserves_protected_workspace_boundary():
    frozen = FrozenChildCatalog(
        tools=(
            {
                "name": "file_write",
                "version": "1",
                "permissions": ["workspace_write"],
            },
        ),
        tool_digest="sha256:read-tools",
        skills=(),
        skill_digest="sha256:skills",
    )
    scope = EffectiveDelegationScope(
        actions=("workspace_write",),
        resources=("task://task-1/workspace/**",),
        effect_kinds=("workspace_write",),
        tools=("file_write",),
        allowed_purposes=("research",),
        workspace_write_roots=("task://task-1/workspace/**",),
        private_staging_root=".astra/subagents/exec-1/staging",
    )
    context = DelegatedExecutionContext(
        task_id="task-1",
        run_id="run-1",
        agent_execution_id="exec-1",
        identity_id="child-1",
        parent_identity_id="parent-1",
        delegation_id="delegation-1",
        delegation_chain=("parent-1", "child-1"),
        purpose="research",
        effective_scope=scope,
        budget_envelope=SubagentBudgetEnvelope(),
        workspace_scope={
            "read_roots": [],
            "write_roots": ["task://task-1/workspace/**"],
            "private_staging_root": scope.private_staging_root,
        },
        tool_catalog_digest=frozen.tool_digest,
        skill_catalog_digest=frozen.skill_digest,
    )
    plan = ActionEffectPlan(
        tool_name="file_write",
        tool_version="1",
        summary="Read protected metadata",
        effects=[
            EffectItem(
                kind="workspace_write",
                resource="task://task-1/workspace/.git/config",
            )
        ],
        required_permissions=["workspace_write"],
        analyzer_version="1",
    )
    decision = ChildInvocationAuthorizer().authorize(
        context=context,
        frozen_catalog=frozen,
        tool_name="file_write",
        tool_version="1",
        effect_plan=plan,
        effect_plan_hash="sha256:protected",
        tool_input={"path": ".git/config"},
        declared_permissions=["workspace_write"],
        execution_mode="request_approval",
        policies=_allow_policy("workspace_write"),
    )
    assert decision.decision.decision.value == "deny"
    assert decision.decision.explanation.reason_code == "protected_resource"
