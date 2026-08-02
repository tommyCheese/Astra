from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.db.models import (
    AgentTurnRecord,
    NodeExecutionRecord,
    PlanRecord,
    ToolCallRecord,
)
from app.repositories.agent_executions import AgentExecutionRepository
from app.repositories.permissions import PermissionRepository
from app.repositories.runs import RunRepository
from app.runner.model_client import MockModelClient
from app.schemas.agent import (
    AgentDecision,
    AgentReflection,
    EffectiveSubagentPolicy,
    ExpectedObservation,
    PlanDraft,
    PlanNodeDraft,
    SubagentBudgetPolicy,
)
from app.schemas.permissions import PermissionPolicySet, PermissionRule
from app.schemas.subagents import (
    DelegatedExecutionContext,
    DelegationContract,
    DelegationRequest,
    DelegationScope,
    EffectiveDelegationScope,
    SubagentBudgetEnvelope,
    SubagentContextManifest,
    SubagentContinuationAnswer,
    SubagentExecutionStatus,
)
from app.subagents.executor import AgentExecutorRuntime, LocalAstraAgentExecutor
from app.subagents.governance import FrozenChildCatalog
from app.subagents.runtime import SubagentRuntimeOperations
from app.tools.base import (
    ArtifactRef,
    Tool,
    ToolRegistry,
    ToolResultEnvelope,
    ToolSpec,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class ReadTool(Tool):
    spec = ToolSpec(
        name="web_search",
        version="1",
        input_schema={"type": "object"},
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        },
        permission="network_read",
        side_effect_level="read_only",
        permissions=["network_read"],
    )

    def __init__(self, *, artifact: bool = False, invalid: bool = False):
        self.artifact = artifact
        self.invalid = invalid
        self.last_context = None

    async def run(self, tool_input, *, context=None):
        self.last_context = context
        if self.invalid:
            return {"not": "an envelope"}
        artifacts = (
            [
                ArtifactRef(
                    id="artifact-1",
                    type="report",
                    mime_type="text/markdown",
                    content_url="/api/artifacts/artifact-1/content",
                    size_bytes=100_000,
                    checksum="sha256:artifact",
                    metadata={"filename": "report.md"},
                )
            ]
            if self.artifact
            else []
        )
        return ToolResultEnvelope(
            data={"value": "evidence"}, artifacts=artifacts
        ).model_dump(mode="json")


class CredentialTool(Tool):
    spec = ToolSpec(
        name="credential.read",
        version="1",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
        permission="credential_use",
        side_effect_level="read_only",
        permissions=["credential_use"],
    )

    def __init__(self):
        self.called = False

    async def run(self, tool_input, *, context=None):
        self.called = True
        return ToolResultEnvelope(data={}).model_dump(mode="json")


class ScriptedChildClient(MockModelClient):
    def __init__(self, decisions: Iterable[AgentDecision]):
        self.decisions = list(decisions)
        self.contexts = []
        self.reflect_calls = 0

    async def plan(self, goal, *, contract):
        return PlanDraft(
            nodes=[
                PlanNodeDraft(
                    node_key="child-step",
                    title="Child work",
                    intent=goal,
                    expected_outcome=ExpectedObservation(
                        kind="child_result", success_condition="typed result returned"
                    ),
                    success_criteria_refs=[item.id for item in contract.success_criteria],
                )
            ]
        )

    async def decide_with_answer(
        self, goal, context, *, on_delta=None, on_reasoning_delta=None
    ):
        self.contexts.append(context)
        return self.decisions.pop(0), None

    async def reflect(self, goal, context):
        self.reflect_calls += 1
        return AgentReflection(
            trigger="tool_failure",
            summary="Tool failed safely.",
            next_action="stop",
        )


def _allow_delegation() -> PermissionPolicySet:
    return PermissionPolicySet(
        version="test",
        rules=[
            PermissionRule(
                id="allow-delegation",
                source="test",
                tier="run",
                decision="allow",
                actions=["delegation_create"],
                resources=["identity://*"],
                reason_code="test_allow",
            )
        ],
    )


async def _child_runtime(session, tool: Tool, *, max_model_calls: int = 5):
    run = await RunRepository(session).create_task_run("Child executor", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    assert root is not None
    permissions = PermissionRepository(session)
    tool_name = tool.spec.name
    if tool_name == "web_search":
        actions = ("network_read",)
        resources = ("web://search/**",)
        effects = ("network_read",)
        destinations = ("web://search/**",)
    else:
        actions = ("credential_use",)
        resources = ("credential://records",)
        effects = ("credential_use",)
        destinations = ()
    parent_scope = {
        "actions": list(actions),
        "resources": list(resources),
        "effect_kinds": list(effects),
        "tools": [tool_name],
        "skills": [],
        "credential_scopes": ["records.read"],
        "data_labels": [],
        "allowed_purposes": ["research"],
        "network_destinations": list(destinations),
        "workspace_read_roots": [],
        "workspace_write_roots": [],
    }
    parent = await permissions.create_identity(
        identity_type="main_agent",
        principal="parent",
        run_id=run.id,
        attributes={"permission_scope": parent_scope},
    )
    root.identity_id = parent.id
    child_identity = await permissions.create_identity(
        identity_type="subagent",
        principal="child",
        run_id=run.id,
        parent_identity_id=parent.id,
        attributes={"permission_scope": parent_scope},
    )
    delegation = await permissions.create_delegation(
        parent_identity_id=parent.id,
        child_identity_id=child_identity.id,
        delegated_scope=parent_scope,
        policies=_allow_delegation(),
    )
    request = DelegationRequest(
        request_id="child-request",
        objective="Research the delegated subject",
        success_criteria=["Return one finding"],
        scope=DelegationScope(included=["subject:one"]),
        output_schema={
            "type": "object",
            "properties": {"finding": {"type": "string"}},
            "required": ["finding"],
        },
        requested_tools=[tool_name],
        resource_scope={"purpose": "research"},
        budget=SubagentBudgetEnvelope(
            max_tokens=8_000,
            max_model_calls=max_model_calls,
            max_tool_calls=3,
            max_wall_time_ms=120_000,
            max_cost_usd=0.5,
        ),
        dedupe_key="child:one",
    )
    contract = DelegationContract(
        contract_id="dc-child",
        contract_hash="sha256:contract",
        task_id=run.task_id,
        run_id=run.id,
        parent_execution_id=root.id,
        depth=1,
        request=request,
        created_at=NOW,
    )
    catalog = FrozenChildCatalog(
        tools=(tool.spec.model_dump(mode="json"),),
        tool_digest="sha256:tools",
        skills=(),
        skill_digest="sha256:skills",
    )
    child = await AgentExecutionRepository(session).create_child(
        contract=contract,
        identity_id=child_identity.id,
        delegation_id=delegation.id,
        catalog_snapshot=catalog.model_dump(),
    )
    scope = EffectiveDelegationScope(
        actions=actions,
        resources=resources,
        effect_kinds=effects,
        tools=(tool_name,),
        credential_scopes=("records.read",),
        allowed_purposes=("research",),
        network_destinations=destinations,
        private_staging_root=f".astra/subagents/{child.id}/staging",
    )
    workspace_scope = {
        "read_roots": [],
        "write_roots": [],
        "private_staging_root": scope.private_staging_root,
    }
    context = DelegatedExecutionContext(
        task_id=run.task_id,
        run_id=run.id,
        agent_execution_id=child.id,
        identity_id=child_identity.id,
        parent_identity_id=parent.id,
        delegation_id=delegation.id,
        delegation_chain=(parent.id, child_identity.id),
        purpose="research",
        effective_scope=scope,
        budget_envelope=request.budget,
        workspace_scope=workspace_scope,
        tool_catalog_digest=catalog.tool_digest,
        skill_catalog_digest=catalog.skill_digest,
    )
    manifest = SubagentContextManifest(
        agent_execution_id=child.id,
        purpose="research",
        tool_catalog_digest=catalog.tool_digest,
        skill_catalog_digest=catalog.skill_digest,
        workspace_scope=workspace_scope,
        created_at=NOW,
    )
    await session.commit()
    registry = ToolRegistry()
    registry.register(tool)
    runtime = AgentExecutorRuntime(
        session=session,
        execution_context=context,
        frozen_catalog=catalog,
        worker_id="test-worker",
    )
    return child, contract, manifest, runtime, registry


async def test_local_child_executes_tool_with_full_lineage_and_completes(session):
    tool = ReadTool(artifact=True)
    child, contract, manifest, runtime, registry = await _child_runtime(session, tool)
    client = ScriptedChildClient(
        [
            AgentDecision(
                decision_type="call_tool",
                reasoning_summary="Search first",
                tool_name="web_search",
                tool_input={"query": "astra"},
            ),
            AgentDecision(
                decision_type="finalize",
                reasoning_summary="Return verified result",
                node_result={"outputs": {"finding": "Astra result"}},
            ),
        ]
    )
    result = await LocalAstraAgentExecutor(
        model_client=client, tool_registry=registry
    ).execute(contract=contract, context_manifest=manifest, runtime=runtime)

    plans = list(
        (await session.scalars(select(PlanRecord).where(PlanRecord.run_id == child.run_id))).all()
    )
    node_executions = list(
        (
            await session.scalars(
                select(NodeExecutionRecord).where(
                    NodeExecutionRecord.agent_execution_id == child.id
                )
            )
        ).all()
    )
    calls = list(
        (
            await session.scalars(
                select(ToolCallRecord).where(ToolCallRecord.agent_execution_id == child.id)
            )
        ).all()
    )
    turns = list(
        (
            await session.scalars(
                select(AgentTurnRecord).where(AgentTurnRecord.agent_execution_id == child.id)
            )
        ).all()
    )
    await session.refresh(child)

    assert result.status == SubagentExecutionStatus.completed
    assert result.outputs == {"finding": "Astra result"}
    assert result.artifacts[0].id == "artifact-1"
    assert child.status == "completed"
    assert plans[0].agent_execution_id == child.id
    assert node_executions[0].status == "completed"
    assert calls[0].agent_execution_id == child.id
    assert all(turn.agent_execution_id == child.id for turn in turns)
    assert tool.last_context.agent_execution_id == child.id
    assert tool.last_context.delegation_context.identity_id == child.identity_id


@pytest.mark.parametrize(
    ("payload", "expected_status"),
    [
        ({"outputs": {}}, SubagentExecutionStatus.failed),
        (
            {"outputs": {"finding": "ok"}, "warnings": ["partial source coverage"]},
            SubagentExecutionStatus.completed_with_warnings,
        ),
        (
            {"outputs": {"finding": "ok"}},
            SubagentExecutionStatus.completed,
        ),
    ],
)
async def test_local_child_validates_schema_warning_and_success_outcomes(
    session, payload, expected_status
):
    tool = ReadTool()
    _, contract, manifest, runtime, registry = await _child_runtime(session, tool)
    client = ScriptedChildClient(
        [
            AgentDecision(
                decision_type="finalize",
                reasoning_summary="Finish",
                node_result=payload,
            )
        ]
    )
    result = await LocalAstraAgentExecutor(
        model_client=client, tool_registry=registry
    ).execute(contract=contract, context_manifest=manifest, runtime=runtime)
    assert result.status == expected_status


async def test_local_child_waits_for_parent_with_structured_question(session):
    tool = ReadTool()
    child, contract, manifest, runtime, registry = await _child_runtime(session, tool)
    client = ScriptedChildClient(
        [
            AgentDecision(
                decision_type="waiting_parent",
                reasoning_summary="Need one bounded input",
                node_result={
                    "question": {
                        "prompt": "Which region?",
                        "required_fields": ["region"],
                        "continuation_token": "v1.token",
                        "round_trip": 1,
                    }
                },
            )
        ]
    )
    result = await LocalAstraAgentExecutor(
        model_client=client, tool_registry=registry
    ).execute(contract=contract, context_manifest=manifest, runtime=runtime)
    await session.refresh(child)
    assert result.status == SubagentExecutionStatus.waiting_parent
    assert result.question.required_fields == ["region"]
    assert child.status == "waiting_parent"


async def test_local_child_waits_for_approval_without_invoking_credential_tool(session):
    tool = CredentialTool()
    child, contract, manifest, runtime, registry = await _child_runtime(session, tool)
    client = ScriptedChildClient(
        [
            AgentDecision(
                decision_type="call_tool",
                reasoning_summary="Credential access is required",
                tool_name="credential.read",
                tool_input={"service": "records"},
            )
        ]
    )
    result = await LocalAstraAgentExecutor(
        model_client=client, tool_registry=registry
    ).execute(contract=contract, context_manifest=manifest, runtime=runtime)
    await session.refresh(child)
    assert result.status == SubagentExecutionStatus.waiting_approval
    assert child.status == "waiting_approval"
    assert tool.called is False


async def test_local_child_reflects_on_invalid_tool_result_then_fails_safely(session):
    tool = ReadTool(invalid=True)
    child, contract, manifest, runtime, registry = await _child_runtime(
        session, tool, max_model_calls=4
    )
    client = ScriptedChildClient(
        [
            AgentDecision(
                decision_type="call_tool",
                reasoning_summary="Try the tool",
                tool_name="web_search",
                tool_input={"query": "astra"},
            ),
            AgentDecision(
                decision_type="fail",
                reasoning_summary="Tool result was invalid",
                node_result={"open_issues": ["invalid_tool_result"]},
            ),
        ]
    )
    result = await LocalAstraAgentExecutor(
        model_client=client, tool_registry=registry
    ).execute(contract=contract, context_manifest=manifest, runtime=runtime)
    await session.refresh(child)
    assert result.status == SubagentExecutionStatus.failed
    assert client.reflect_calls == 1
    assert child.status == "failed"


@pytest.mark.parametrize(
    ("decision_type", "expected_status"),
    [
        ("blocked", SubagentExecutionStatus.blocked),
        ("waiting_resource", SubagentExecutionStatus.waiting_resource),
    ],
)
async def test_local_child_persists_blocked_and_waiting_resource_states(
    session, decision_type, expected_status
):
    tool = ReadTool()
    child, contract, manifest, runtime, registry = await _child_runtime(session, tool)
    client = ScriptedChildClient(
        [
            AgentDecision(
                decision_type=decision_type,
                reasoning_summary=f"Child entered {decision_type}",
                node_result={
                    "open_issues": ["bounded_issue"],
                    "reason": "resource_conflict",
                },
            )
        ]
    )
    result = await LocalAstraAgentExecutor(
        model_client=client, tool_registry=registry
    ).execute(contract=contract, context_manifest=manifest, runtime=runtime)
    await session.refresh(child)
    assert result.status == expected_status
    assert child.status == expected_status.value


async def _operations_runtime(session, *, enabled: bool = True):
    run = await RunRepository(session).create_task_run("Runtime operations", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    assert root is not None
    permissions = PermissionRepository(session)
    parent_scope = {
        "actions": ["network_read"],
        "resources": ["web://search/**"],
        "effect_kinds": ["network_read"],
        "tools": ["web_search"],
        "skills": [],
        "credential_scopes": [],
        "data_labels": [],
        "allowed_purposes": ["research"],
        "network_destinations": ["web://search/**"],
        "workspace_read_roots": [],
        "workspace_write_roots": [],
        "max_tool_calls": 4,
        "max_runtime_seconds": 120,
    }
    parent = await permissions.create_identity(
        identity_type="main_agent",
        principal="parent",
        run_id=run.id,
        attributes={"permission_scope": parent_scope},
    )
    root.identity_id = parent.id
    tool = ReadTool()
    await permissions.freeze_tool_catalog(
        run.id,
        catalog=[tool.spec.model_dump(mode="json")],
        digest="sha256:parent-tools",
    )
    await session.commit()
    policy = EffectiveSubagentPolicy(
        enabled=enabled,
        read_only=True,
        budgets=SubagentBudgetPolicy(
            max_children_total=2,
            max_children_per_parent=2,
            max_parallel_children=1,
            max_depth=1,
            max_parent_round_trips=1,
            max_wall_time_seconds=120,
            max_tokens=8_000,
            max_model_calls=5,
            max_tool_calls=4,
            max_cost_usd=0.5,
        ),
    )
    operations = SubagentRuntimeOperations(
        session,
        policy=policy,
        permission_policies=_allow_delegation(),
        task_policy_scope=parent_scope,
        continuation_secret="test-continuation-secret",
    )
    root.budget_envelope = {
        "max_tokens": 16_000,
        "max_model_calls": 10,
        "max_tool_calls": 8,
        "max_wall_time_seconds": 240,
        "max_cost_usd": 1,
        "max_children": 2,
    }
    await session.commit()
    request = DelegationRequest(
        request_id="ops-child",
        objective="Research the delegated subject",
        success_criteria=["Return one finding"],
        scope=DelegationScope(included=["subject:ops"]),
        output_schema={
            "type": "object",
            "properties": {"finding": {"type": "string"}},
            "required": ["finding"],
        },
        requested_tools=["web_search"],
        resource_scope={
            "purpose": "research",
            "actions": ["network_read"],
            "resources": ["web://search/**"],
            "effect_kinds": ["network_read"],
            "tools": ["web_search"],
            "data_labels": [],
            "allowed_purposes": ["research"],
            "network_destinations": ["web://search/**"],
            "workspace_read_roots": [],
            "workspace_write_roots": [],
            "max_tool_calls": 4,
            "max_runtime_seconds": 120,
        },
        budget=SubagentBudgetEnvelope(
            max_tokens=8_000,
            max_model_calls=5,
            max_tool_calls=4,
            max_wall_time_ms=120_000,
            max_cost_usd=0.5,
        ),
        dedupe_key="ops:child",
    )
    registry = ToolRegistry()
    registry.register(tool)
    return run, root, parent, operations, request, registry


async def test_runtime_operations_delegate_inspect_continue_collect_and_cancel(session):
    _, root, parent, operations, request, registry = await _operations_runtime(session)
    child = await operations.delegate_task(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        request=request,
    )
    inspected = await operations.inspect_delegation(child.id)
    assert inspected["status"] == "queued"
    with pytest.raises(Exception, match="one active child"):
        await operations.delegate_task(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=request.model_copy(
                update={"request_id": "second", "dedupe_key": "ops:second"}
            ),
        )

    stored = child.context_manifest
    manifest = SubagentContextManifest.model_validate(stored["manifest"])
    waiting_client = ScriptedChildClient(
        [
            AgentDecision(
                decision_type="waiting_parent",
                reasoning_summary="Need region",
                node_result={
                    "question": {
                        "prompt": "Which region?",
                        "required_fields": ["region"],
                    }
                },
            )
        ]
    )
    waiting = await LocalAstraAgentExecutor(
        model_client=waiting_client, tool_registry=registry
    ).execute(
        contract=DelegationContract.model_validate(child.contract),
        context_manifest=manifest,
        runtime=await operations.executor_runtime(child.id, worker_id="ops-worker"),
    )
    resumed = await operations.respond_to_parent_question(
        answer=SubagentContinuationAnswer(
            agent_execution_id=child.id,
            continuation_token=waiting.question.continuation_token,
            round_trip=waiting.question.round_trip,
            values={"region": "EU"},
            answered_at=datetime.now(timezone.utc),
        )
    )
    assert resumed.status == "queued"

    final_client = ScriptedChildClient(
        [
            AgentDecision(
                decision_type="finalize",
                reasoning_summary="Completed after parent answer",
                node_result={"outputs": {"finding": "EU finding"}},
            )
        ]
    )
    completed = await LocalAstraAgentExecutor(
        model_client=final_client, tool_registry=registry
    ).execute(
        contract=DelegationContract.model_validate(child.contract),
        context_manifest=manifest,
        runtime=await operations.executor_runtime(child.id, worker_id="ops-worker-2"),
    )
    collected = await operations.collect_delegation_results(
        parent_execution_id=root.id,
        execution_ids=[child.id],
    )
    assert completed.status == SubagentExecutionStatus.completed
    assert final_client.contexts[0]["continuation_answers"][0]["values"] == {
        "region": "EU"
    }
    assert collected[0]["terminal"] is True

    second_request = request.model_copy(
        update={"request_id": "cancel-child", "dedupe_key": "ops:cancel"}
    )
    second = await operations.delegate_task(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        request=second_request,
    )
    cancelled = await operations.cancel_delegation(second.id)
    assert cancelled == [second.id]
    assert (await operations.inspect_delegation(second.id))["status"] == "cancelled"


async def test_disabled_delegation_leaves_root_only_run_unchanged(session):
    run, root, parent, operations, request, _ = await _operations_runtime(
        session, enabled=False
    )
    original_status = run.status
    original_root_version = root.state_version
    with pytest.raises(Exception, match="disabled"):
        await operations.delegate_task(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=request,
        )
    assert await AgentExecutionRepository(session).descendants(root.id) == []
    await session.refresh(run)
    await session.refresh(root)
    assert run.status == original_status
    assert root.state_version == original_root_version


async def test_runtime_rejects_non_beneficial_atomic_delegation(session):
    _, root, parent, operations, request, _ = await _operations_runtime(session)
    atomic = request.model_copy(
        update={
            "requested_tools": [],
            "resource_scope": {
                **request.resource_scope,
                "tools": [],
                "delegation_gate": {"simple_atomic": True},
            },
        }
    )
    with pytest.raises(Exception, match="benefit gate"):
        await operations.delegate_task(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=atomic,
        )
    assert await AgentExecutionRepository(session).descendants(root.id) == []
