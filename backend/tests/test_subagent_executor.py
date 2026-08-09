from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.application.context_compaction.child import compact_child_context
from app.application.subagents.executor import AgentExecutorRuntime, LocalAstraAgentExecutor
from app.application.subagents.governance import FrozenChildCatalog
from app.application.subagents.lifecycle import SubagentCancellationService
from app.application.subagents.runtime import SubagentRuntimeOperations
from app.application.subagents.scope import DelegationAuthorizationError
from app.application.subagents.supervisor import SubagentSupervisor
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.execution_state import AgentDecision, AgentReflection
from app.common.schemas.agent.planning import ExpectedObservation, PlanDraft, PlanNodeDraft
from app.common.schemas.agent.run_policy import EffectiveSubagentPolicy, SubagentBudgetPolicy
from app.common.schemas.context_compaction import parse_child_checkpoint
from app.common.schemas.permissions import PermissionPolicySet, PermissionRule
from app.common.schemas.subagents import (
    DelegatedExecutionContext,
    DelegationContract,
    DelegationRequest,
    DelegationScope,
    EffectiveDelegationScope,
    SubagentBudgetEnvelope,
    SubagentContextManifest,
    SubagentContinuationAnswer,
    SubagentExecutionStatus,
    SubagentFanoutRequest,
    SubagentJoinPolicy,
    SubagentJoinSpec,
)
from app.infrastructure.db.model_base import AstraOrmRecordBase
from app.infrastructure.db.models.executions import NodeExecutionRecord
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.plans import PlanRecord
from app.infrastructure.db.models.runs import AgentTurnRecord
from app.infrastructure.model_clients.mock import MockModelClient
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.tool_settings import (
    ToolSettingsRepository,
    default_tool_states,
)
from app.infrastructure.tools.base import (
    AstraTool,
    AstraToolRegistry,
    AstraToolSpec,
    ToolArtifactReference,
    ToolResultEnvelope,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class ReadTool(AstraTool):
    spec = AstraToolSpec(
        name="catalog_search",
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
                ToolArtifactReference(
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
        return ToolResultEnvelope(data={"value": "evidence"}, artifacts=artifacts).model_dump(mode="json")


class CredentialTool(AstraTool):
    spec = AstraToolSpec(
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
                    expected_outcome=ExpectedObservation(kind="child_result", success_condition="typed result returned"),
                    success_criteria_refs=[item.id for item in contract.success_criteria],
                )
            ]
        )

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        self.contexts.append(context)
        return self.decisions.pop(0), None

    async def reflect(self, goal, context):
        self.reflect_calls += 1
        return AgentReflection(
            trigger="tool_failure",
            summary="AstraTool failed safely.",
            next_action="stop",
        )


class BlockingDecisionClient(ScriptedChildClient):
    def __init__(self, decision: AgentDecision, session, entered, release):
        super().__init__([decision])
        self.session = session
        self.entered = entered
        self.release = release
        self.transaction_states = []

    async def decide_with_answer(self, goal, context, *, on_delta=None, on_reasoning_delta=None):
        self.transaction_states.append(self.session.in_transaction())
        self.entered.set()
        await self.release.wait()
        return await super().decide_with_answer(
            goal,
            context,
            on_delta=on_delta,
            on_reasoning_delta=on_reasoning_delta,
        )


class BlockingReadTool(ReadTool):
    def __init__(self, session, entered, release):
        super().__init__()
        self.session = session
        self.entered = entered
        self.release = release
        self.transaction_states = []

    async def run(self, tool_input, *, context=None):
        self.transaction_states.append(self.session.in_transaction())
        self.entered.set()
        await self.release.wait()
        return await super().run(tool_input, context=context)


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


async def _child_runtime(session, tool: AstraTool, *, max_model_calls: int = 5):
    run = await RunUnitOfWork(session).create_task_run("Child executor", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    assert root is not None
    permissions = PermissionRepository(session)
    tool_name = tool.spec.name
    if tool_name == "catalog_search":
        actions = ("network_read",)
        resources = ("provider://astra.builtin/catalog_search",)
        effects = ("network_read",)
        destinations = ("provider://astra.builtin/catalog_search",)
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
    registry = AstraToolRegistry()
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
                tool_name="catalog_search",
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
        model_client=client,
        tool_registry=registry,
        settings=AstraRuntimeSettings().model_copy(
            update={
                "context_compaction_child_inline_bytes": 1,
                "context_compaction_child_inline_tokens": 1,
            }
        ),
    ).execute(contract=contract, context_manifest=manifest, runtime=runtime)

    plans = list((await session.scalars(select(PlanRecord).where(PlanRecord.run_id == child.run_id))).all())
    node_executions = list(
        (await session.scalars(select(NodeExecutionRecord).where(NodeExecutionRecord.agent_execution_id == child.id))).all()
    )
    calls = list((await session.scalars(select(ToolCallRecord).where(ToolCallRecord.agent_execution_id == child.id))).all())
    turns = list((await session.scalars(select(AgentTurnRecord).where(AgentTurnRecord.agent_execution_id == child.id))).all())
    await session.refresh(child)

    assert result.status == SubagentExecutionStatus.completed
    assert result.outputs == {"finding": "Astra result"}
    assert result.artifacts[0].id == "artifact-1"
    assert child.status == "completed"
    assert plans[0].agent_execution_id == child.id
    assert node_executions[0].status == "completed"
    assert calls[0].agent_execution_id == child.id
    assert calls[0].output["data"] == {"value": "evidence"}
    normalized = turns[0].observation["data"]["normalized_output"]
    assert normalized["externalized"] is True
    assert normalized["reference"]["ref"] == f"tool_call:{calls[0].id}"
    assert client.contexts[0]["context_checkpoint"]["agent_execution_id"] == child.id
    assert client.contexts[1]["context_checkpoint"]["manifest_hash"]
    assert all(turn.agent_execution_id == child.id for turn in turns)
    assert tool.last_context.agent_execution_id == child.id
    assert tool.last_context.delegation_context.identity_id == child.identity_id


@pytest.mark.asyncio
async def test_child_context_compaction_uses_an_independent_automatic_window(session):
    tool = ReadTool()
    child, contract, manifest, _runtime, _registry = await _child_runtime(session, tool)
    settings = AstraRuntimeSettings(
        model_provider="mock",
        context_window_fallback_tokens=16_384,
        context_output_reserve_tokens=1_024,
        context_compaction_output_reserve_tokens=512,
        context_auto_compact_ratio=0.5,
        context_compaction_recovery_ratio=0.3,
        context_compaction_v2_enabled=True,
        context_compaction_child_enabled=True,
        context_compaction_shadow_mode=False,
    )
    observations = [{"kind": "tool_result", "summary": f"result-{index}", "data": {"text": "界" * 2_000}} for index in range(6)]
    initial = parse_child_checkpoint(
        {
            "agent_execution_id": child.id,
            "manifest_hash": "sha256:unused-v1-hash",
            "local_summary": "child initialized",
            "continuation_round_trips": 1,
            "continuation_answers": [
                {
                    "agent_execution_id": child.id,
                    "continuation_token": "signed-token",
                    "round_trip": 1,
                    "values": {"scope": "continue"},
                    "answered_at": NOW.isoformat(),
                }
            ],
            "created_at": NOW.isoformat(),
        }
    )

    execution, checkpoint, visible = await compact_child_context(
        session=session,
        settings=settings,
        model_client=MockModelClient(),
        execution=child,
        contract=contract,
        manifest=manifest,
        plan={"id": "child-plan", "version": 1, "nodes": []},
        usage={"model_calls": 0},
        observations=observations,
        checkpoint=initial,
    )

    assert checkpoint.checkpoint_role == "child_execution"
    assert len(visible) < len(observations)
    assert execution.checkpoint["context_compaction"]["source_item_ids"]
    assert execution.checkpoint["context_continuation"]["contract_hash"] == contract.contract_hash
    assert checkpoint.continuation_round_trips == 1
    assert checkpoint.continuation_answers[0].values == {"scope": "continue"}


async def _commit_competing_writer(sessions, run_id: str, marker: str) -> None:
    async with sessions() as writer:
        await RunUnitOfWork(writer).add_event(run_id, marker, {"writer": "sibling"})
        await writer.commit()


async def test_child_model_wait_releases_transaction_for_competing_writer(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'child-model-wait.db'}",
        connect_args={"timeout": 0.2},
    )
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entered = asyncio.Event()
    release = asyncio.Event()
    async with sessions() as child_session:
        tool = ReadTool()
        child, contract, manifest, runtime, registry = await _child_runtime(child_session, tool)
        client = BlockingDecisionClient(
            AgentDecision(
                decision_type="finalize",
                reasoning_summary="Finish after the contention probe",
                node_result={"outputs": {"finding": "model wait remained nonblocking"}},
            ),
            child_session,
            entered,
            release,
        )
        running = asyncio.create_task(
            LocalAstraAgentExecutor(
                model_client=client,
                tool_registry=registry,
            ).execute(contract=contract, context_manifest=manifest, runtime=runtime)
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        try:
            await asyncio.wait_for(
                _commit_competing_writer(sessions, child.run_id, "test.model_wait.writer"),
                timeout=1,
            )
        finally:
            release.set()
        result = await running
        assert result.status == SubagentExecutionStatus.completed
        assert client.transaction_states == [False]
    await engine.dispose()


async def test_child_tool_wait_releases_transaction_for_competing_writer(tmp_path):
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'child-tool-wait.db'}",
        connect_args={"timeout": 0.2},
    )
    async with engine.begin() as connection:
        await connection.run_sync(AstraOrmRecordBase.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    entered = asyncio.Event()
    release = asyncio.Event()
    async with sessions() as child_session:
        tool = BlockingReadTool(child_session, entered, release)
        child, contract, manifest, runtime, registry = await _child_runtime(child_session, tool)
        client = ScriptedChildClient(
            [
                AgentDecision(
                    decision_type="call_tool",
                    reasoning_summary="Run the blocking read probe",
                    tool_name="catalog_search",
                    tool_input={"query": "contention"},
                ),
                AgentDecision(
                    decision_type="finalize",
                    reasoning_summary="Finish after the tool probe",
                    node_result={"outputs": {"finding": "tool wait remained nonblocking"}},
                ),
            ]
        )
        running = asyncio.create_task(
            LocalAstraAgentExecutor(
                model_client=client,
                tool_registry=registry,
            ).execute(contract=contract, context_manifest=manifest, runtime=runtime)
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        try:
            await asyncio.wait_for(
                _commit_competing_writer(sessions, child.run_id, "test.tool_wait.writer"),
                timeout=1,
            )
        finally:
            release.set()
        result = await running
        assert result.status == SubagentExecutionStatus.completed
        assert tool.transaction_states == [False]
    await engine.dispose()


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
async def test_local_child_validates_schema_warning_and_success_outcomes(session, payload, expected_status):
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
    result = await LocalAstraAgentExecutor(model_client=client, tool_registry=registry).execute(
        contract=contract, context_manifest=manifest, runtime=runtime
    )
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
    result = await LocalAstraAgentExecutor(model_client=client, tool_registry=registry).execute(
        contract=contract, context_manifest=manifest, runtime=runtime
    )
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
    result = await LocalAstraAgentExecutor(model_client=client, tool_registry=registry).execute(
        contract=contract, context_manifest=manifest, runtime=runtime
    )
    await session.refresh(child)
    assert result.status == SubagentExecutionStatus.waiting_approval
    assert child.status == "waiting_approval"
    assert tool.called is False


async def test_local_child_reflects_on_invalid_tool_result_then_fails_safely(session):
    tool = ReadTool(invalid=True)
    child, contract, manifest, runtime, registry = await _child_runtime(session, tool, max_model_calls=4)
    client = ScriptedChildClient(
        [
            AgentDecision(
                decision_type="call_tool",
                reasoning_summary="Try the tool",
                tool_name="catalog_search",
                tool_input={"query": "astra"},
            ),
            AgentDecision(
                decision_type="fail",
                reasoning_summary="AstraTool result was invalid",
                node_result={"open_issues": ["invalid_tool_result"]},
            ),
        ]
    )
    result = await LocalAstraAgentExecutor(model_client=client, tool_registry=registry).execute(
        contract=contract, context_manifest=manifest, runtime=runtime
    )
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
async def test_local_child_persists_blocked_and_waiting_resource_states(session, decision_type, expected_status):
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
    result = await LocalAstraAgentExecutor(model_client=client, tool_registry=registry).execute(
        contract=contract, context_manifest=manifest, runtime=runtime
    )
    await session.refresh(child)
    assert result.status == expected_status
    assert child.status == expected_status.value


async def _operations_runtime(session, *, enabled: bool = True):
    run = await RunUnitOfWork(session).create_task_run("Runtime operations", {})
    root = await AgentExecutionRepository(session).root_for_run(run.id)
    assert root is not None
    permissions = PermissionRepository(session)
    parent_scope = {
        "actions": ["network_read"],
        "resources": ["provider://astra.builtin/catalog_search"],
        "effect_kinds": ["network_read"],
        "tools": ["catalog_search"],
        "skills": [],
        "credential_scopes": [],
        "data_labels": [],
        "allowed_purposes": ["research"],
        "network_destinations": ["provider://astra.builtin/catalog_search"],
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
        requested_tools=["catalog_search"],
        resource_scope={
            "purpose": "research",
            "actions": ["network_read"],
            "resources": ["provider://astra.builtin/catalog_search"],
            "effect_kinds": ["network_read"],
            "tools": ["catalog_search"],
            "data_labels": [],
            "allowed_purposes": ["research"],
            "network_destinations": ["provider://astra.builtin/catalog_search"],
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
    registry = AstraToolRegistry()
    registry.register(tool)
    return run, root, parent, operations, request, registry


async def test_runtime_fanout_creates_two_children_and_one_idempotent_join(session):
    _, root, parent, operations, request, _ = await _operations_runtime(session)
    policy = operations.policy.model_copy(
        update={
            "rollout_cohort": "trusted_read_only",
            "budgets": operations.policy.budgets.model_copy(update={"max_parallel_children": 2}),
        }
    )
    operations = SubagentRuntimeOperations(
        session,
        policy=policy,
        permission_policies=_allow_delegation(),
        task_policy_scope=parent.attributes["permission_scope"],
    )
    fanout = SubagentFanoutRequest(
        group_id="group:research",
        tasks=[
            request,
            request.model_copy(
                update={
                    "request_id": "ops-child-2",
                    "dedupe_key": "ops:child:2",
                    "scope": DelegationScope(included=["subject:ops:second"]),
                }
            ),
        ],
        join=SubagentJoinSpec(
            key="join:research",
            policy=SubagentJoinPolicy.required,
        ),
    )

    accepted = await operations.delegate_tasks(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        fanout=fanout,
    )
    replay = await operations.delegate_tasks(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        fanout=fanout,
    )

    assert len(accepted.child_execution_ids) == 2
    assert replay.child_execution_ids == accepted.child_execution_ids
    assert replay.join_id == accepted.join_id
    assert replay.idempotent_replay is True
    assert len(await AgentExecutionRepository(session).descendants(root.id)) == 2


async def test_rollout_drill_shadow_canary_kill_switch_drain_and_immutable_effects(session):
    run, root, parent, operations, request, _ = await _operations_runtime(session)
    runtime_kwargs = {
        "permission_policies": _allow_delegation(),
        "task_policy_scope": parent.attributes["permission_scope"],
    }

    shadow = SubagentRuntimeOperations(
        session,
        policy=operations.policy.model_copy(update={"rollout_cohort": "shadow"}),
        **runtime_kwargs,
    )
    with pytest.raises(DelegationAuthorizationError, match="Shadow cohort"):
        await shadow.delegate_task(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=request,
        )
    assert await AgentExecutionRepository(session).descendants(root.id) == []
    assert "subagent.shadow_decision" in {event.type for event in await RunUnitOfWork(session).list_events(run.id)}

    canary_policy = operations.policy.model_copy(update={"rollout_cohort": "trusted_read_only", "read_only": True})
    canary = SubagentRuntimeOperations(session, policy=canary_policy, **runtime_kwargs)
    child = await canary.delegate_task(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        request=request,
    )
    assert child.status == "queued"
    assert child.context_manifest["execution_context"]["effective_scope"]["actions"] == ["network_read"]

    killed = SubagentRuntimeOperations(
        session,
        policy=canary_policy.model_copy(update={"kill_switch": True}),
        **runtime_kwargs,
    )
    with pytest.raises(DelegationAuthorizationError, match="disabled"):
        await killed.delegate_task(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=request.model_copy(update={"request_id": "after-kill", "dedupe_key": "after-kill"}),
        )
    assert len(await AgentExecutionRepository(session).descendants(root.id)) == 1

    runs = RunUnitOfWork(session)
    effect = await runs.start_tool_call(
        run.id,
        None,
        "external.publish",
        "1",
        {"value": "already-published"},
        "external_write",
        "external_write",
        agent_execution_id=child.id,
    )
    await runs.finish_tool_call(effect.id, output={"remote_id": "immutable-result"})
    await session.commit()

    drain = await SubagentCancellationService(session).cancel_tree(child.id, reason="kill_switch_drain")
    assert drain.cancelled_execution_ids == (child.id,)
    assert drain.immutable_effects[0]["output"] == {"remote_id": "immutable-result"}
    drained = await AgentExecutionRepository(session).require(child.id)
    assert drained.status == "cancelled"
    assert drained.error["immutable_effects"] == list(drain.immutable_effects)


async def test_live_swarm_switch_blocks_new_children_for_running_supervisor(session):
    run, root, parent, operations, request, registry = await _operations_runtime(session)
    settings = AstraRuntimeSettings(
        model_provider="mock",
        agent_subagent_rollout_cohort="trusted_read_only",
    )
    tool_settings = ToolSettingsRepository(session)
    await tool_settings.get_or_create(default_tool_states(settings))
    await tool_settings.set_all({"swarm": False}, default_tool_states(settings))
    await session.commit()
    supervisor = SubagentSupervisor(
        settings=settings,
        session=session,
        session_factory=async_sessionmaker(
            session.bind,
            expire_on_commit=False,
            class_=type(session),
        ),
        run_id=run.id,
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        policy=operations.policy.model_copy(update={"rollout_cohort": "trusted_read_only"}),
        tool_registry=registry,
        model_client_factory=MockModelClient,
    )
    fanout = SubagentFanoutRequest(
        group_id="group:disabled-live",
        tasks=[request],
        join=SubagentJoinSpec(
            key="join:disabled-live",
            policy=SubagentJoinPolicy.required,
        ),
    )

    with pytest.raises(ValueError, match="disabled by the user"):
        await supervisor.delegate_tasks(fanout)

    assert await AgentExecutionRepository(session).descendants(root.id) == []


async def test_runtime_operations_delegate_inspect_continue_collect_and_cancel(session):
    _, root, parent, operations, request, registry = await _operations_runtime(session)
    child = await operations.delegate_task(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        request=request,
    )
    inspected = await operations.inspect_delegation(child.id)
    assert inspected["status"] == "queued"
    with pytest.raises(Exception, match="active child limit"):
        await operations.delegate_task(
            parent_execution_id=root.id,
            parent_identity_id=parent.id,
            request=request.model_copy(update={"request_id": "second", "dedupe_key": "ops:second"}),
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
    waiting = await LocalAstraAgentExecutor(model_client=waiting_client, tool_registry=registry).execute(
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
    completed = await LocalAstraAgentExecutor(model_client=final_client, tool_registry=registry).execute(
        contract=DelegationContract.model_validate(child.contract),
        context_manifest=manifest,
        runtime=await operations.executor_runtime(child.id, worker_id="ops-worker-2"),
    )
    collected = await operations.collect_delegation_results(
        parent_execution_id=root.id,
        execution_ids=[child.id],
    )
    assert completed.status == SubagentExecutionStatus.completed
    assert final_client.contexts[0]["continuation_answers"][0]["values"] == {"region": "EU"}
    assert collected[0]["terminal"] is True

    second_request = request.model_copy(update={"request_id": "cancel-child", "dedupe_key": "ops:cancel"})
    second = await operations.delegate_task(
        parent_execution_id=root.id,
        parent_identity_id=parent.id,
        request=second_request,
    )
    cancelled = await operations.cancel_delegation(second.id)
    assert cancelled == [second.id]
    assert (await operations.inspect_delegation(second.id))["status"] == "cancelled"


async def test_disabled_delegation_leaves_root_only_run_unchanged(session):
    run, root, parent, operations, request, _ = await _operations_runtime(session, enabled=False)
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
