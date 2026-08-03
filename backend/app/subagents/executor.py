from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.context_compaction.tool_outputs import ToolOutputGovernanceService
from app.core.config import Settings
from app.db.models import AgentExecutionRecord, utc_now
from app.permissions.effects import DefaultEffectAnalyzer, effect_plan_hash
from app.repositories.agent_executions import AgentExecutionRepository
from app.repositories.executions import NodeExecutionRepository
from app.repositories.plans import PlanRepository
from app.repositories.runs import RunRepository
from app.runner.model_client import ModelClient
from app.runner.reasoning import CompletionGate
from app.schemas.agent import (
    CriterionStatus,
    NodeExecutionPhase,
    NodeExecutionStatus,
    PlanNodeStatus,
    SuccessCriterion,
    TaskContract,
    ValidationIssue,
    ValidationOutcome,
)
from app.schemas.context_compaction import (
    ChildCheckpoint,
    ContextOwnerRole,
    ContextReference,
    parse_child_checkpoint,
)
from app.schemas.permissions import PermissionDecisionKind, PermissionPolicySet
from app.schemas.subagents import (
    DelegatedExecutionContext,
    DelegationContract,
    SubagentArtifactReference,
    SubagentContextCheckpoint,
    SubagentContextManifest,
    SubagentEvidenceReference,
    SubagentExecutionStatus,
    SubagentQuestion,
    SubagentResult,
)
from app.subagents.budget import HierarchicalBudgetManager
from app.subagents.context import SubagentContinuationService
from app.subagents.governance import ChildInvocationAuthorizer, FrozenChildCatalog, stable_digest
from app.tools.base import (
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
    validate_json_schema,
    validate_tool_result,
)

EventCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class AgentExecutorRuntime:
    session: AsyncSession
    execution_context: DelegatedExecutionContext
    frozen_catalog: FrozenChildCatalog
    permission_policies: PermissionPolicySet | None = None
    worker_id: str = "local-subagent"
    artifact_service: Any = None
    sandbox_service: Any = None
    on_event: EventCallback | None = None
    continuation_service: SubagentContinuationService | None = None
    budget_manager: HierarchicalBudgetManager | None = None


class AgentExecutor(ABC):
    """Adapter boundary all local and future remote subagent executors must obey."""

    @abstractmethod
    async def execute(
        self,
        *,
        contract: DelegationContract,
        context_manifest: SubagentContextManifest,
        runtime: AgentExecutorRuntime,
        checkpoint: dict[str, Any] | None = None,
    ) -> SubagentResult:
        raise NotImplementedError


class LocalAstraAgentExecutor(AgentExecutor):
    """A child-local Astra loop for the conservative depth-one, read-only slice."""

    def __init__(
        self,
        *,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        settings: Settings | None = None,
    ):
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.settings = settings or Settings()
        self.authorizer = ChildInvocationAuthorizer()
        self.completion_gate = CompletionGate()

    async def execute(
        self,
        *,
        contract: DelegationContract,
        context_manifest: SubagentContextManifest,
        runtime: AgentExecutorRuntime,
        checkpoint: dict[str, Any] | None = None,
    ) -> SubagentResult:
        self._validate_runtime(contract, context_manifest, runtime)
        self.model_client.bind_agent_execution(
            runtime.execution_context.agent_execution_id
        )
        executions = AgentExecutionRepository(runtime.session)
        execution = await executions.require(runtime.execution_context.agent_execution_id)
        if execution.status == SubagentExecutionStatus.queued.value:
            execution = await executions.claim(
                execution.id,
                worker_id=runtime.worker_id,
                expected_state_version=execution.state_version,
                expected_cancellation_epoch=execution.cancellation_epoch,
            )
            await runtime.session.commit()
        elif execution.status != SubagentExecutionStatus.running.value:
            raise ValueError(f"Child execution is not runnable: {execution.status}")
        repo = RunRepository(runtime.session)
        plans = PlanRepository(runtime.session)
        usage = deepcopy(execution.budget_usage or {})
        usage.setdefault("model_calls", 0)
        usage.setdefault("tool_calls", 0)
        observations = list((checkpoint or execution.checkpoint or {}).get("observations", []))
        raw_context_checkpoint = (checkpoint or execution.checkpoint or {}).get(
            "context_checkpoint"
        )
        context_checkpoint: ChildCheckpoint = (
            parse_child_checkpoint(raw_context_checkpoint)
            if raw_context_checkpoint
            else SubagentContextCheckpoint(
                agent_execution_id=execution.id,
                manifest_hash=stable_digest(context_manifest.model_dump(mode="json")),
                local_summary="Child execution initialized.",
                created_at=utc_now(),
            )
        )
        artifact_refs: list[SubagentArtifactReference] = []
        evidence_refs: list[SubagentEvidenceReference] = []

        plan = await plans.active_for_run(
            contract.run_id,
            agent_execution_id=execution.id,
        )
        if plan is None:
            task_contract = self._task_contract(contract)
            draft = await self.model_client.plan(
                contract.request.objective,
                contract=task_contract,
            )
            usage["model_calls"] += 1
            plan = await plans.create(
                contract.run_id,
                draft,
                agent_execution_id=execution.id,
            )
            await self._event(repo, runtime, "subagent.plan.created", {"plan_id": plan.id})
            await runtime.session.commit()
        else:
            task_contract = self._task_contract(contract)

        node_execution = None
        while usage["model_calls"] < contract.request.budget.max_model_calls:
            plan = await plans.require(plan.id)
            active_node = self._next_node(plan)
            node_execution = None
            if active_node is not None:
                node_execution = await self._node_execution(
                    runtime.session,
                    execution=execution,
                    plan=plan,
                    node=active_node,
                    worker_id=runtime.worker_id,
                )
            model_context = {
                "agent_execution_id": execution.id,
                "delegation_contract": contract.model_dump(mode="json"),
                "context_manifest": context_manifest.model_dump(mode="json"),
                "task_contract": task_contract.model_dump(mode="json"),
                "plan": self._plan_context(plan),
                "active_node": self._node_context(active_node),
                "observations": deepcopy(observations),
                "tool_manifests": {
                    item["name"]: item for item in runtime.frozen_catalog.tools
                },
                "skill_catalog": [deepcopy(item) for item in runtime.frozen_catalog.skills],
                "budget": contract.request.budget.model_dump(mode="json"),
                "budget_usage": deepcopy(usage),
                "continuation_answers": [
                    item.model_dump(mode="json")
                    for item in context_checkpoint.continuation_answers
                ],
                "context_checkpoint": context_checkpoint.model_dump(mode="json"),
            }
            decision, _ = await self.model_client.decide_with_answer(
                contract.request.objective,
                model_context,
            )
            usage["model_calls"] += 1
            turn = await repo.create_agent_turn(
                contract.run_id,
                int(usage["model_calls"]),
                decision.decision_type,
                decision.reasoning_summary,
                selected_tool=decision.tool_name,
                decision=decision.model_dump(mode="json"),
                state_version_before=execution.state_version,
                plan_version=plan.version,
                phase="prepared",
                plan_node_id=active_node.id if active_node else None,
                node_execution_id=node_execution.id if node_execution else None,
                agent_execution_id=execution.id,
            )
            if decision.decision_type == "waiting_parent":
                raw_question = decision.node_result.get("question") or decision.tool_input
                if runtime.continuation_service is not None:
                    question = runtime.continuation_service.question(
                        checkpoint=context_checkpoint,
                        prompt=str(raw_question.get("prompt", "Parent input is required.")),
                        required_fields=list(raw_question.get("required_fields", [])),
                    )
                else:
                    question = SubagentQuestion.model_validate(raw_question)
                result = SubagentResult(
                    status=SubagentExecutionStatus.waiting_parent,
                    summary=decision.reasoning_summary,
                    question=question,
                    usage=usage,
                    provenance=self._provenance(execution, contract),
                )
                await repo.update_agent_turn(turn.id, status="waiting", phase="waiting_parent")
                current_execution = await executions.require(execution.id)
                current_execution.checkpoint = {
                    **(current_execution.checkpoint or {}),
                    "observations": deepcopy(observations),
                    "context_checkpoint": context_checkpoint.model_dump(mode="json"),
                }
                current_execution.budget_usage = deepcopy(usage)
                await runtime.session.flush()
                if node_execution is not None:
                    current_node_execution = await NodeExecutionRepository(
                        runtime.session
                    ).require(node_execution.id)
                    await NodeExecutionRepository(runtime.session).transition(
                        current_node_execution.id,
                        expected_version=current_node_execution.state_version,
                        phase=NodeExecutionPhase.result_unknown,
                        status=NodeExecutionStatus.waiting,
                        wait_reason="parent_input",
                        checkpoint={"question": question.model_dump(mode="json")},
                    )
                await self._transition_waiting(
                    executions, execution, result, "waiting_parent", "parent_input"
                )
                await runtime.session.commit()
                return result
            if decision.decision_type == "waiting_resource":
                reason = str(decision.node_result.get("reason") or "resource_conflict")
                result = SubagentResult(
                    status=SubagentExecutionStatus.waiting_resource,
                    summary=decision.reasoning_summary,
                    open_issues=[reason],
                    usage=usage,
                    provenance=self._provenance(execution, contract),
                )
                await repo.update_agent_turn(turn.id, status="waiting", phase="waiting_resource")
                if node_execution is not None:
                    nodes = NodeExecutionRepository(runtime.session)
                    current_node_execution = await nodes.require(node_execution.id)
                    await nodes.transition(
                        current_node_execution.id,
                        expected_version=current_node_execution.state_version,
                        phase=NodeExecutionPhase.waiting_resource,
                        status=NodeExecutionStatus.waiting,
                        wait_reason=reason,
                    )
                await self._transition_waiting(
                    executions, execution, result, "waiting_resource", reason
                )
                await runtime.session.commit()
                return result
            if decision.decision_type in {"blocked", "fail"}:
                status = (
                    SubagentExecutionStatus.blocked
                    if decision.decision_type == "blocked"
                    else SubagentExecutionStatus.failed
                )
                result = SubagentResult(
                    status=status,
                    summary=decision.reasoning_summary,
                    open_issues=list(decision.node_result.get("open_issues", [])),
                    usage=usage,
                    provenance=self._provenance(execution, contract),
                )
                await repo.update_agent_turn(turn.id, status="completed", phase="terminal")
                await self._finish_node_execution(runtime.session, node_execution, result)
                await self._terminal(executions, execution, result)
                await self._settle_budget(runtime, execution.id, result)
                await runtime.session.commit()
                return result
            if decision.decision_type == "call_tool" and decision.tool_name:
                outcome = await self._call_tool(
                    repo=repo,
                    executions=executions,
                    execution=execution,
                    runtime=runtime,
                    contract=contract,
                    turn_id=turn.id,
                    plan_node_id=active_node.id if active_node else None,
                    node_execution_id=node_execution.id if node_execution else None,
                    tool_name=decision.tool_name,
                    tool_input=decision.tool_input,
                    usage=usage,
                )
                if isinstance(outcome, SubagentResult):
                    await runtime.session.commit()
                    return outcome
                observations.append(outcome)
                for item in outcome.get("artifacts", []):
                    try:
                        artifact_refs.append(self._artifact_reference(item))
                    except ValueError:
                        continue
                for ref in outcome.get("evidence_refs", []):
                    evidence_refs.append(
                        SubagentEvidenceReference(id=str(ref), summary="Child tool evidence")
                    )
                await repo.update_agent_turn(
                    turn.id,
                    status="completed" if outcome["status"] == "succeeded" else "failed",
                    phase="committed" if outcome["status"] == "succeeded" else "failed",
                    observation=outcome,
                    tool_call_id=outcome.get("tool_call_id"),
                )
                if outcome["status"] == "failed":
                    reflection = await self.model_client.reflect(
                        contract.request.objective,
                        {**model_context, "latest_observation": outcome},
                    )
                    usage["model_calls"] += 1
                    await repo.update_agent_turn(
                        turn.id,
                        reflection=reflection.model_dump(mode="json"),
                        phase="reflected",
                    )
                execution = await self._checkpoint(
                    executions,
                    execution,
                    runtime,
                    usage,
                    observations,
                    plan.id,
                    context_checkpoint,
                )
                await runtime.session.commit()
                continue
            if decision.decision_type == "complete_node" and active_node is not None:
                if active_node.status == PlanNodeStatus.pending.value:
                    await plans.transition_node(active_node.id, PlanNodeStatus.running)
                await plans.transition_node(
                    active_node.id,
                    PlanNodeStatus.completed,
                    evidence_refs=[item.id for item in evidence_refs],
                )
                if node_execution is not None:
                    node_execution = await NodeExecutionRepository(runtime.session).require(
                        node_execution.id
                    )
                    await NodeExecutionRepository(runtime.session).transition(
                        node_execution.id,
                        expected_version=node_execution.state_version,
                        phase=NodeExecutionPhase.completed,
                        status=NodeExecutionStatus.completed,
                        result={"summary": decision.reasoning_summary},
                    )
                await repo.update_agent_turn(turn.id, status="completed", phase="committed")
                await runtime.session.commit()
                continue
            if decision.decision_type in {"finalize", "complete"}:
                result = self._result_from_decision(
                    decision.node_result,
                    summary=decision.reasoning_summary,
                    execution=execution,
                    contract=contract,
                    usage=usage,
                    artifacts=artifact_refs,
                    evidence=evidence_refs,
                )
                await self._finish_node_execution(
                    runtime.session,
                    node_execution,
                    result,
                )
                if active_node is not None and result.status in {
                    SubagentExecutionStatus.completed,
                    SubagentExecutionStatus.completed_with_warnings,
                }:
                    current_node = await plans.require_node(active_node.id)
                    if current_node.status == PlanNodeStatus.pending.value:
                        current_node = await plans.transition_node(
                            active_node.id, PlanNodeStatus.running
                        )
                    if current_node.status == PlanNodeStatus.running.value:
                        await plans.transition_node(
                            active_node.id,
                            PlanNodeStatus.completed,
                            evidence_refs=[item.id for item in evidence_refs],
                        )
                await repo.update_agent_turn(turn.id, status="completed", phase="terminal")
                await self._terminal(executions, execution, result)
                await self._settle_budget(runtime, execution.id, result)
                await runtime.session.commit()
                return result
            observation = {
                "kind": "decision_rejected",
                "status": "failed",
                "summary": "Child decision must call an allowed tool, complete a node, wait, or finalize.",
            }
            observations.append(observation)
            await repo.update_agent_turn(
                turn.id, status="failed", phase="failed", observation=observation
            )
        result = SubagentResult(
            status=SubagentExecutionStatus.failed,
            summary="Child model-call budget was exhausted before a valid result.",
            open_issues=["model_call_budget_exhausted"],
            usage=usage,
            provenance=self._provenance(execution, contract),
        )
        await self._finish_node_execution(runtime.session, node_execution, result)
        await self._terminal(executions, execution, result)
        await self._settle_budget(runtime, execution.id, result)
        await runtime.session.commit()
        return result

    async def _call_tool(
        self,
        *,
        repo: RunRepository,
        executions: AgentExecutionRepository,
        execution: AgentExecutionRecord,
        runtime: AgentExecutorRuntime,
        contract: DelegationContract,
        turn_id: str,
        plan_node_id: str | None,
        node_execution_id: str | None,
        tool_name: str,
        tool_input: dict[str, Any],
        usage: dict[str, Any],
    ) -> dict[str, Any] | SubagentResult:
        if usage["tool_calls"] >= contract.request.budget.max_tool_calls:
            return {
                "kind": "tool_budget_exhausted",
                "status": "failed",
                "summary": "Child tool-call budget was exhausted.",
            }
        tool = self.tool_registry.get(tool_name)
        if tool.spec.side_effect_level not in {"none", "read", "read_only"}:
            return {
                "kind": "tool_selection_rejected",
                "status": "failed",
                "summary": "The first subagent slice permits only read-only tools.",
                "error": {"category": "subagent_read_only_violation"},
            }
        effect_plan = DefaultEffectAnalyzer().analyze(
            tool.spec,
            tool_input,
            task_id=contract.task_id,
        )
        authorization = self.authorizer.authorize(
            context=runtime.execution_context,
            frozen_catalog=runtime.frozen_catalog,
            tool_name=tool.spec.name,
            tool_version=tool.spec.version,
            effect_plan=effect_plan,
            effect_plan_hash=effect_plan_hash(effect_plan),
            tool_input=tool_input,
            declared_permissions=tool.spec.permissions,
            execution_mode="request_approval",
            policies=runtime.permission_policies,
            tool_identity=f"{tool.spec.provider_id}:{tool.spec.name}@{tool.spec.version}",
        )
        if authorization.decision.decision == PermissionDecisionKind.deny:
            return {
                "kind": "permission_denied",
                "status": "failed",
                "summary": authorization.decision.explanation.reason_code,
            }
        if authorization.decision.decision == PermissionDecisionKind.ask:
            approval_token = str(uuid.uuid4())
            call = await repo.start_tool_call(
                contract.run_id,
                None,
                tool.spec.name,
                tool.spec.version,
                tool_input,
                tool.spec.permission,
                tool.spec.side_effect_level,
                plan_node_id=plan_node_id,
                node_execution_id=node_execution_id,
                agent_execution_id=execution.id,
                status="awaiting_approval",
            )
            approval = await repo.create_approval_request(
                run_id=contract.run_id,
                turn_id=turn_id,
                tool_call_id=call.id,
                tool_name=tool.spec.name,
                tool_version=tool.spec.version,
                frozen_input=tool_input,
                input_hash=stable_digest(tool_input),
                preview=f"{tool.spec.name}: delegated invocation",
                permission=", ".join(effect_plan.required_permissions),
                impact=max(
                    (effect.risk for effect in effect_plan.effects),
                    default=tool.spec.side_effect_level,
                ),
                similar_matcher=None,
                frozen_effect_plan=effect_plan.model_dump(mode="json"),
                effect_plan_hash=effect_plan_hash(effect_plan),
                analyzer_version=effect_plan.analyzer_version,
                analyzer_digest=effect_plan.analyzer_digest,
                agent_execution_id=execution.id,
                requester_identity_id=runtime.execution_context.identity_id,
                delegation_id=runtime.execution_context.delegation_id,
                catalog_digest=runtime.frozen_catalog.tool_digest,
                continuation_token=approval_token,
                grant_scope={
                    "parent_identity_id": runtime.execution_context.parent_identity_id,
                    "delegation_chain": list(runtime.execution_context.delegation_chain),
                    "purpose": runtime.execution_context.purpose,
                },
                node_execution_id=node_execution_id,
            )
            result = SubagentResult(
                status=SubagentExecutionStatus.waiting_approval,
                summary="Child tool call requires parent/user approval.",
                open_issues=[authorization.decision.explanation.reason_code],
                usage=usage,
                provenance={
                    **self._provenance(execution, contract),
                    "approval_id": approval.id,
                    "continuation_token": approval_token,
                },
            )
            await repo.update_agent_turn(turn_id, status="waiting", phase="waiting_approval")
            await self._transition_waiting(
                executions,
                execution,
                result,
                "waiting_approval",
                authorization.decision.explanation.reason_code,
            )
            if node_execution_id is not None:
                nodes = NodeExecutionRepository(runtime.session)
                node_execution = await nodes.require(node_execution_id)
                await nodes.transition(
                    node_execution.id,
                    expected_version=node_execution.state_version,
                    phase=NodeExecutionPhase.waiting_approval,
                    status=NodeExecutionStatus.waiting,
                    wait_reason=authorization.decision.explanation.reason_code,
                    checkpoint={
                        "effect_plan": effect_plan.model_dump(mode="json"),
                        "effect_plan_hash": effect_plan_hash(effect_plan),
                    },
                )
            return result
        call = await repo.start_tool_call(
            contract.run_id,
            None,
            tool.spec.name,
            tool.spec.version,
            tool_input,
            tool.spec.permission,
            tool.spec.side_effect_level,
            plan_node_id=plan_node_id,
            node_execution_id=node_execution_id,
            agent_execution_id=execution.id,
        )
        try:
            raw_output = await tool.run(
                tool_input,
                context=ToolExecutionContext(
                    run_id=contract.run_id,
                    tool_call_id=call.id,
                    step_id=plan_node_id,
                    trace_id=f"{contract.run_id}:{execution.id}:{call.id}",
                    artifact_service=runtime.artifact_service,
                    sandbox_service=runtime.sandbox_service,
                    task_id=contract.task_id,
                    effect_plan=effect_plan.model_dump(mode="json"),
                    runtime_identity_id=runtime.execution_context.identity_id,
                    agent_execution_id=execution.id,
                    delegation_context=runtime.execution_context,
                ),
            )
            output = validate_tool_result(raw_output, tool.spec).model_dump(mode="json")
            await repo.finish_tool_call(call.id, output=output)
            reference_labels = tuple(
                dict.fromkeys(
                    label
                    for effect in effect_plan.effects
                    for label in effect.data_labels
                )
            )

            async def persisted_tool_output_reference(
                _serialized: bytes,
                checksum: str,
                *,
                call_id: str = call.id,
                data_labels: tuple[str, ...] = reference_labels,
            ) -> ContextReference:
                return ContextReference(
                    kind="tool_call",
                    ref=f"tool_call:{call_id}",
                    content_hash=checksum,
                    data_labels=data_labels,
                    allowed_purposes=("child_agent_context", "child_result_validation"),
                )

            governed_output = await ToolOutputGovernanceService(self.settings).normalize(
                role=ContextOwnerRole.child_execution,
                tool_name=tool.spec.name,
                status="succeeded",
                output=output,
                key_fields={
                    "tool_call_id": call.id,
                    "agent_execution_id": execution.id,
                    "identity_id": runtime.execution_context.identity_id,
                },
                persist=persisted_tool_output_reference,
            )
            usage["tool_calls"] += 1
            return {
                "kind": "tool_result",
                "status": "succeeded",
                "summary": f"{tool.spec.name} completed.",
                "tool_call_id": call.id,
                "data": (
                    {"normalized_output": governed_output.model_dump(mode="json", exclude_none=True)}
                    if governed_output.externalized
                    else output.get("data", {})
                ),
                "artifacts": output.get("artifacts", []),
                "evidence_refs": [call.id],
            }
        except (ToolExecutionError, ValueError) as exc:
            error = (
                exc.to_payload()
                if isinstance(exc, ToolExecutionError)
                else {"category": "invalid_result", "message": str(exc)}
            )
            await repo.finish_tool_call(call.id, error=error)
            return {
                "kind": "tool_result",
                "status": "failed",
                "summary": error["message"],
                "tool_call_id": call.id,
                "error": error,
            }

    def _result_from_decision(
        self,
        payload: dict[str, Any],
        *,
        summary: str,
        execution: AgentExecutionRecord,
        contract: DelegationContract,
        usage: dict[str, Any],
        artifacts: list[SubagentArtifactReference],
        evidence: list[SubagentEvidenceReference],
    ) -> SubagentResult:
        outputs = deepcopy(payload.get("outputs", {}))
        outcomes: list[ValidationOutcome] = []
        try:
            validate_json_schema(outputs, contract.request.output_schema, path="outputs")
            outcomes.append(ValidationOutcome(validator="subagent_output_schema", passed=True))
        except ValueError as exc:
            outcomes.append(
                ValidationOutcome(
                    validator="subagent_output_schema",
                    passed=False,
                    blocking=True,
                    issues=[
                        ValidationIssue(
                            code="subagent_output_schema_invalid",
                            message=str(exc),
                            severity="error",
                        )
                    ],
                )
            )
        completion = self.completion_gate.evaluate_basic(validation_outcomes=outcomes)
        if completion.state.value == "blocked":
            return SubagentResult(
                status=SubagentExecutionStatus.failed,
                summary="Child output failed schema validation.",
                outputs=outputs,
                open_issues=completion.unmet_criteria,
                completion=completion.model_dump(mode="json"),
                usage=usage,
                provenance=self._provenance(execution, contract),
            )
        warnings = list(payload.get("warnings", []))
        return SubagentResult(
            status=(
                SubagentExecutionStatus.completed_with_warnings
                if warnings
                else SubagentExecutionStatus.completed
            ),
            summary=str(payload.get("summary") or summary),
            outputs=outputs,
            artifacts=[
                *artifacts,
                *[
                    self._artifact_reference(item)
                    for item in payload.get("artifacts", [])
                ],
            ],
            evidence_refs=[
                *evidence,
                *[
                    SubagentEvidenceReference.model_validate(item)
                    for item in payload.get("evidence_refs", [])
                ],
            ],
            claims=list(payload.get("claims", [])),
            open_issues=list(payload.get("open_issues", [])),
            completion=completion.model_dump(mode="json"),
            usage=usage,
            provenance=self._provenance(execution, contract),
        )

    @staticmethod
    def _artifact_reference(item: dict[str, Any]) -> SubagentArtifactReference:
        if "uri" in item:
            return SubagentArtifactReference.model_validate(item)
        artifact_id = str(item["id"])
        metadata = item.get("metadata") or {}
        return SubagentArtifactReference(
            id=artifact_id,
            uri=str(item.get("content_url") or f"artifact://{artifact_id}"),
            name=metadata.get("filename"),
            mime_type=item.get("mime_type"),
            content_hash=item.get("checksum"),
        )

    @staticmethod
    async def _finish_node_execution(
        session: AsyncSession,
        node_execution,
        result: SubagentResult,
    ) -> None:
        if node_execution is None:
            return
        repository = NodeExecutionRepository(session)
        current = await repository.require(node_execution.id)
        if current.status in {
            NodeExecutionStatus.completed.value,
            NodeExecutionStatus.failed.value,
            NodeExecutionStatus.blocked.value,
            NodeExecutionStatus.cancelled.value,
        }:
            return
        success = result.status in {
            SubagentExecutionStatus.completed,
            SubagentExecutionStatus.completed_with_warnings,
        }
        await repository.transition(
            current.id,
            expected_version=current.state_version,
            phase=(NodeExecutionPhase.completed if success else NodeExecutionPhase.failed),
            status=(NodeExecutionStatus.completed if success else NodeExecutionStatus.failed),
            result=result.model_dump(mode="json") if success else None,
            failure=(
                None
                if success
                else {
                    "category": (
                        result.open_issues[0] if result.open_issues else "child_failed"
                    )
                }
            ),
        )

    @staticmethod
    async def _node_execution(
        session: AsyncSession,
        *,
        execution: AgentExecutionRecord,
        plan,
        node,
        worker_id: str,
    ):
        from sqlalchemy import select

        from app.db.models import NodeExecutionRecord

        repository = NodeExecutionRepository(session)
        current = await session.scalar(
            select(NodeExecutionRecord).where(
                NodeExecutionRecord.agent_execution_id == execution.id,
                NodeExecutionRecord.plan_node_id == node.id,
                NodeExecutionRecord.current_slot == "current",
            )
        )
        if current is not None:
            return current
        current = await repository.create_claim(
            run_id=execution.run_id,
            plan_id=plan.id,
            plan_version=plan.version,
            plan_node_id=node.id,
            worker_id=worker_id,
            agent_execution_id=execution.id,
        )
        return await repository.transition(
            current.id,
            expected_version=current.state_version,
            phase=NodeExecutionPhase.running,
        )

    @staticmethod
    def _task_contract(contract: DelegationContract) -> TaskContract:
        return TaskContract(
            original_goal=contract.request.objective,
            deliverables=list(contract.request.output_schema.get("properties", {})),
            constraints=[
                f"included:{item}" for item in contract.request.scope.included
            ]
            + [f"excluded:{item}" for item in contract.request.scope.excluded],
            prohibited_actions=["publish_final_answer", "modify_parent_state"],
            success_criteria=[
                SuccessCriterion(
                    id=f"child-criterion-{index}",
                    description=description,
                    verification_method="delegation_output_schema",
                    status=CriterionStatus.pending,
                    provenance={"delegation_contract": contract.contract_id},
                )
                for index, description in enumerate(
                    contract.request.success_criteria, start=1
                )
            ],
            risk_level="low",
        )

    @staticmethod
    def _next_node(plan):
        completed = {
            node.node_key for node in plan.nodes if node.status == PlanNodeStatus.completed.value
        }
        dependencies = {
            node.id: {
                next(item.node_key for item in plan.nodes if item.id == edge.predecessor_id)
                for edge in plan.edges
                if edge.successor_id == node.id
            }
            for node in plan.nodes
        }
        return next(
            (
                node
                for node in sorted(plan.nodes, key=lambda item: item.index)
                if node.status in {PlanNodeStatus.pending.value, PlanNodeStatus.running.value}
                and dependencies[node.id] <= completed
            ),
            None,
        )

    @staticmethod
    def _plan_context(plan) -> dict[str, Any]:
        return {
            "id": plan.id,
            "version": plan.version,
            "nodes": [LocalAstraAgentExecutor._node_context(node) for node in plan.nodes],
        }

    @staticmethod
    def _node_context(node) -> dict[str, Any] | None:
        if node is None:
            return None
        return {
            "id": node.id,
            "node_key": node.node_key,
            "intent": node.intent,
            "status": node.status,
            "required_capabilities": list(node.required_capabilities or []),
        }

    async def _checkpoint(
        self,
        executions: AgentExecutionRepository,
        execution: AgentExecutionRecord,
        runtime: AgentExecutorRuntime,
        usage: dict[str, Any],
        observations: list[dict[str, Any]],
        plan_id: str,
        context_checkpoint: ChildCheckpoint,
    ) -> AgentExecutionRecord:
        return await executions.save_checkpoint(
            execution.id,
            worker_id=runtime.worker_id,
            fencing_token=execution.fencing_token,
            expected_state_version=execution.state_version,
            checkpoint={
                "schema_version": 1,
                "runtime_version": "astra-subagent-v1",
                "plan_id": plan_id,
                "observations": deepcopy(observations),
                "resume_safe": True,
                "contract_hash": execution.contract.get("contract_hash"),
                "tool_catalog_digest": runtime.frozen_catalog.tool_digest,
                "skill_catalog_digest": runtime.frozen_catalog.skill_digest,
                "context_checkpoint": context_checkpoint.model_dump(mode="json"),
            },
            budget_usage=usage,
            cancellation_epoch=execution.cancellation_epoch,
        )

    async def _transition_waiting(
        self,
        executions: AgentExecutionRepository,
        execution: AgentExecutionRecord,
        result: SubagentResult,
        status: str,
        reason: str,
    ) -> None:
        current = await executions.require(execution.id)
        await executions.transition(
            execution.id,
            expected_state_version=current.state_version,
            expected_fencing_token=current.fencing_token,
            expected_cancellation_epoch=execution.cancellation_epoch,
            status=status,
            phase=status,
            wait_reason=reason,
            result=result.model_dump(mode="json"),
        )

    async def _terminal(
        self,
        executions: AgentExecutionRepository,
        execution: AgentExecutionRecord,
        result: SubagentResult,
    ) -> None:
        current = await executions.require(execution.id)
        if current.status == SubagentExecutionStatus.running.value:
            current = await executions.transition(
                execution.id,
                expected_state_version=current.state_version,
                expected_fencing_token=current.fencing_token,
                expected_cancellation_epoch=execution.cancellation_epoch,
                status=SubagentExecutionStatus.completing,
                phase="completing",
            )
        await executions.transition(
            execution.id,
            expected_state_version=current.state_version,
            expected_fencing_token=current.fencing_token,
            expected_cancellation_epoch=execution.cancellation_epoch,
            status=result.status,
            phase="terminal",
            result=result.model_dump(mode="json"),
        )

    @staticmethod
    async def _settle_budget(
        runtime: AgentExecutorRuntime,
        execution_id: str,
        result: SubagentResult,
    ) -> None:
        if runtime.budget_manager is not None:
            await runtime.budget_manager.settle(
                execution_id,
                actual_usage=result.usage,
                commit=False,
            )

    async def _event(
        self,
        repo: RunRepository,
        runtime: AgentExecutorRuntime,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        enriched = {
            **payload,
            "agent_execution_id": runtime.execution_context.agent_execution_id,
        }
        await repo.add_event(
            runtime.execution_context.run_id,
            event_type,
            enriched,
            agent_execution_id=runtime.execution_context.agent_execution_id,
        )
        if runtime.on_event is not None:
            await runtime.on_event(event_type, enriched)

    @staticmethod
    def _provenance(
        execution: AgentExecutionRecord,
        contract: DelegationContract,
    ) -> dict[str, Any]:
        return {
            "agent_execution_id": execution.id,
            "identity_id": execution.identity_id,
            "delegation_id": execution.delegation_id,
            "contract_id": contract.contract_id,
            "contract_hash": contract.contract_hash,
        }

    @staticmethod
    def _validate_runtime(
        contract: DelegationContract,
        manifest: SubagentContextManifest,
        runtime: AgentExecutorRuntime,
    ) -> None:
        context = runtime.execution_context
        if (
            context.task_id != contract.task_id
            or context.run_id != contract.run_id
            or manifest.agent_execution_id != context.agent_execution_id
            or manifest.tool_catalog_digest != runtime.frozen_catalog.tool_digest
            or context.tool_catalog_digest != runtime.frozen_catalog.tool_digest
        ):
            raise ValueError("Child executor runtime does not match the frozen delegation")
        if contract.depth != 1:
            raise ValueError("The initial local child executor supports depth one only")
        if context.effective_scope.workspace_write_roots:
            raise ValueError("The initial local child executor is read-only")
