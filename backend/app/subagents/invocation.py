"""Governed tool invocation stage for delegated executions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.context_compaction.tool_outputs import ToolOutputGovernanceService
from app.core.config import Settings
from app.db.models.executions import AgentExecutionRecord
from app.execution.contracts import InvocationIntent
from app.permissions.effects import DefaultEffectAnalyzer, effect_plan_hash
from app.repositories.agent_executions import AgentExecutionRepository
from app.repositories.approval_contracts import ApprovalRequestCreate
from app.repositories.executions import NodeExecutionRepository
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.agent.types import NodeExecutionPhase, NodeExecutionStatus
from app.schemas.context_compaction import ContextOwnerRole, ContextReference
from app.schemas.permissions import PermissionDecisionKind
from app.schemas.subagents import (
    DelegationContract,
    SubagentExecutionStatus,
    SubagentResult,
)
from app.subagents.executor_contracts import AgentExecutorRuntime
from app.subagents.governance import ChildInvocationAuthorizer, stable_digest
from app.tools.base import (
    ToolExecutionContext,
    ToolExecutionError,
    ToolRegistry,
    validate_tool_result,
)


@dataclass(frozen=True)
class ChildToolInvocationInput:
    repository: RunUnitOfWork
    executions: AgentExecutionRepository
    execution: AgentExecutionRecord
    runtime: AgentExecutorRuntime
    contract: DelegationContract
    turn_id: str
    intent: InvocationIntent
    usage: dict[str, Any]


class ChildToolInvocationStage:
    """Apply child catalog/permission attenuation before invoking a tool."""

    def __init__(
        self,
        *,
        settings: Settings,
        tool_registry: ToolRegistry,
        authorizer: ChildInvocationAuthorizer,
    ) -> None:
        self._settings = settings
        self._tool_registry = tool_registry
        self._authorizer = authorizer

    async def execute(
        self,
        action: ChildToolInvocationInput,
    ) -> dict[str, Any] | SubagentResult:
        budget_failure = self._budget_failure(action)
        if budget_failure is not None:
            return budget_failure
        tool = self._tool_registry.get(action.intent.tool_name)
        read_only_failure = self._read_only_failure(tool.spec.side_effect_level)
        if read_only_failure is not None:
            return read_only_failure
        effect_plan = DefaultEffectAnalyzer().analyze(
            tool.spec,
            action.intent.tool_input,
            task_id=action.contract.task_id,
        )
        authorization = self._authorizer.authorize(
            context=action.runtime.execution_context,
            frozen_catalog=action.runtime.frozen_catalog,
            tool_name=tool.spec.name,
            tool_version=tool.spec.version,
            effect_plan=effect_plan,
            effect_plan_hash=effect_plan_hash(effect_plan),
            tool_input=action.intent.tool_input,
            declared_permissions=tool.spec.permissions,
            execution_mode="request_approval",
            policies=action.runtime.permission_policies,
            tool_identity=f"{tool.spec.provider_id}:{tool.spec.name}@{tool.spec.version}",
        )
        decision = authorization.decision.decision
        if decision == PermissionDecisionKind.deny:
            return self._permission_denied(authorization.decision.explanation.reason_code)
        if decision == PermissionDecisionKind.ask:
            return await self._await_approval(
                action,
                tool,
                effect_plan,
                authorization.decision.explanation.reason_code,
            )
        return await self._invoke(action, tool, effect_plan)

    async def _await_approval(
        self,
        action: ChildToolInvocationInput,
        tool: Any,
        effect_plan: Any,
        reason_code: str,
    ) -> SubagentResult:
        approval_token = str(uuid.uuid4())
        call = await self._start_tool_call(
            action,
            tool,
            status="awaiting_approval",
        )
        approval = await action.repository.create_approval_request(
            ApprovalRequestCreate(
                run_id=action.contract.run_id,
                turn_id=action.turn_id,
                tool_call_id=call.id,
                tool_name=tool.spec.name,
                tool_version=tool.spec.version,
                frozen_input=action.intent.tool_input,
                input_hash=stable_digest(action.intent.tool_input),
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
                agent_execution_id=action.execution.id,
                requester_identity_id=action.runtime.execution_context.identity_id,
                delegation_id=action.runtime.execution_context.delegation_id,
                catalog_digest=action.runtime.frozen_catalog.tool_digest,
                continuation_token=approval_token,
                grant_scope=self._grant_scope(action),
                node_execution_id=action.intent.node_execution_id,
            )
        )
        result = SubagentResult(
            status=SubagentExecutionStatus.waiting_approval,
            summary="Child tool call requires parent/user approval.",
            open_issues=[reason_code],
            usage=action.usage,
            provenance={
                "agent_execution_id": action.execution.id,
                "identity_id": action.execution.identity_id,
                "delegation_id": action.execution.delegation_id,
                "contract_id": action.contract.contract_id,
                "contract_hash": action.contract.contract_hash,
                "approval_id": approval.id,
                "continuation_token": approval_token,
            },
        )
        await self._persist_waiting_approval(action, result, reason_code, effect_plan)
        return result

    async def _persist_waiting_approval(
        self,
        action: ChildToolInvocationInput,
        result: SubagentResult,
        reason_code: str,
        effect_plan: Any,
    ) -> None:
        await action.repository.update_agent_turn(
            action.turn_id,
            status="waiting",
            phase="waiting_approval",
        )
        await self._transition_execution_waiting(action, result, reason_code)
        if action.intent.node_execution_id is None:
            return
        nodes = NodeExecutionRepository(action.runtime.session)
        node = await nodes.require(action.intent.node_execution_id)
        await nodes.transition(
            node.id,
            expected_version=node.state_version,
            phase=NodeExecutionPhase.waiting_approval,
            status=NodeExecutionStatus.waiting,
            wait_reason=reason_code,
            checkpoint={
                "effect_plan": effect_plan.model_dump(mode="json"),
                "effect_plan_hash": effect_plan_hash(effect_plan),
            },
        )

    async def _transition_execution_waiting(
        self,
        action: ChildToolInvocationInput,
        result: SubagentResult,
        reason_code: str,
    ) -> None:
        current = await action.executions.require(action.execution.id)
        await action.executions.transition(
            current.id,
            expected_state_version=current.state_version,
            expected_fencing_token=current.fencing_token,
            expected_cancellation_epoch=current.cancellation_epoch,
            status=SubagentExecutionStatus.waiting_approval.value,
            phase="waiting_approval",
            result=result.model_dump(mode="json"),
            wait_reason=reason_code,
        )

    async def _invoke(
        self,
        action: ChildToolInvocationInput,
        tool: Any,
        effect_plan: Any,
    ) -> dict[str, Any]:
        call = await self._start_tool_call(action, tool)
        try:
            raw_output = await tool.run(
                action.intent.tool_input,
                context=self._execution_context(action, call.id, effect_plan),
            )
            output = validate_tool_result(raw_output, tool.spec).model_dump(mode="json")
            await action.repository.finish_tool_call(call.id, output=output)
            governed = await self._govern_output(
                action, tool.spec.name, call.id, output, effect_plan
            )
            action.usage["tool_calls"] += 1
            return self._success_result(tool.spec.name, call.id, output, governed)
        except (ToolExecutionError, ValueError) as exc:
            error = self._error_payload(exc)
            await action.repository.finish_tool_call(call.id, error=error)
            return {
                "kind": "tool_result",
                "status": "failed",
                "summary": error["message"],
                "tool_call_id": call.id,
                "error": error,
            }

    async def _start_tool_call(
        self,
        action: ChildToolInvocationInput,
        tool: Any,
        *,
        status: str = "running",
    ):
        return await action.repository.start_tool_call(
            action.contract.run_id,
            None,
            tool.spec.name,
            tool.spec.version,
            action.intent.tool_input,
            tool.spec.permission,
            tool.spec.side_effect_level,
            plan_node_id=action.intent.plan_node_id,
            node_execution_id=action.intent.node_execution_id,
            agent_execution_id=action.execution.id,
            status=status,
        )

    def _execution_context(
        self,
        action: ChildToolInvocationInput,
        tool_call_id: str,
        effect_plan: Any,
    ) -> ToolExecutionContext:
        return ToolExecutionContext(
            run_id=action.contract.run_id,
            tool_call_id=tool_call_id,
            step_id=action.intent.plan_node_id,
            trace_id=f"{action.contract.run_id}:{action.execution.id}:{tool_call_id}",
            artifact_service=action.runtime.artifact_service,
            sandbox_service=action.runtime.sandbox_service,
            task_id=action.contract.task_id,
            effect_plan=effect_plan.model_dump(mode="json"),
            runtime_identity_id=action.runtime.execution_context.identity_id,
            agent_execution_id=action.execution.id,
            delegation_context=action.runtime.execution_context,
        )

    async def _govern_output(
        self,
        action: ChildToolInvocationInput,
        tool_name: str,
        tool_call_id: str,
        output: dict[str, Any],
        effect_plan: Any,
    ):
        labels = tuple(
            dict.fromkeys(label for effect in effect_plan.effects for label in effect.data_labels)
        )

        async def reference(_serialized: bytes, checksum: str) -> ContextReference:
            return ContextReference(
                kind="tool_call",
                ref=f"tool_call:{tool_call_id}",
                content_hash=checksum,
                data_labels=labels,
                allowed_purposes=("child_agent_context", "child_result_validation"),
            )

        return await ToolOutputGovernanceService(self._settings).normalize(
            role=ContextOwnerRole.child_execution,
            tool_name=tool_name,
            status="succeeded",
            output=output,
            key_fields={
                "tool_call_id": tool_call_id,
                "agent_execution_id": action.execution.id,
                "identity_id": action.runtime.execution_context.identity_id,
            },
            persist=reference,
        )

    @staticmethod
    def _success_result(
        tool_name: str,
        call_id: str,
        output: dict[str, Any],
        governed: Any,
    ) -> dict[str, Any]:
        data = (
            {"normalized_output": governed.model_dump(mode="json", exclude_none=True)}
            if governed.externalized
            else output.get("data", {})
        )
        return {
            "kind": "tool_result",
            "status": "succeeded",
            "summary": f"{tool_name} completed.",
            "tool_call_id": call_id,
            "data": data,
            "artifacts": output.get("artifacts", []),
            "evidence_refs": [call_id],
        }

    @staticmethod
    def _budget_failure(action: ChildToolInvocationInput) -> dict[str, Any] | None:
        if action.usage["tool_calls"] < action.contract.request.budget.max_tool_calls:
            return None
        return {
            "kind": "tool_budget_exhausted",
            "status": "failed",
            "summary": "Child tool-call budget was exhausted.",
        }

    @staticmethod
    def _read_only_failure(side_effect_level: str) -> dict[str, Any] | None:
        if side_effect_level in {"none", "read", "read_only"}:
            return None
        return {
            "kind": "tool_selection_rejected",
            "status": "failed",
            "summary": "The first subagent slice permits only read-only tools.",
            "error": {"category": "subagent_read_only_violation"},
        }

    @staticmethod
    def _permission_denied(reason_code: str) -> dict[str, Any]:
        return {
            "kind": "permission_denied",
            "status": "failed",
            "summary": reason_code,
        }

    @staticmethod
    def _grant_scope(action: ChildToolInvocationInput) -> dict[str, Any]:
        context = action.runtime.execution_context
        return {
            "parent_identity_id": context.parent_identity_id,
            "delegation_chain": list(context.delegation_chain),
            "purpose": context.purpose,
        }

    @staticmethod
    def _error_payload(exc: ToolExecutionError | ValueError) -> dict[str, Any]:
        if isinstance(exc, ToolExecutionError):
            return exc.to_payload()
        return {"category": "invalid_result", "message": str(exc)}
