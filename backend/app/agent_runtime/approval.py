"""Route permission decisions into deny, approval wait, or executable ToolCall."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.core.config import Settings
from app.db.models.permissions import ToolCallRecord
from app.db.models.plans import PlanNodeRecord
from app.db.models.runs import AgentTurnRecord, StepRecord
from app.permissions.effects import grant_proposals
from app.permissions.invocation import InvocationAuthorizationResult
from app.repositories.approval_contracts import ApprovalRequestCreate
from app.repositories.executions import NodeExecutionRepository
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.runner.approvals import input_hash, safe_preview, similar_matcher
from app.schemas.agent.execution_state import AgentDecision
from app.schemas.agent.types import NodeExecutionPhase
from app.schemas.permissions import ActionEffectPlan, PermissionDecisionKind
from app.tools.base import Tool, ToolExecutionError


@dataclass(frozen=True)
class ApprovalStageInput:
    run_id: str
    turn: AgentTurnRecord
    decision: AgentDecision
    tool: Tool
    effect_plan: ActionEffectPlan
    effect_plan_hash: str
    authorization: InvocationAuthorizationResult
    step: StepRecord | PlanNodeRecord | None
    active_node_execution_id: str | None
    has_canonical_plan: bool
    is_approved_resume: bool
    approved_tool_call: ToolCallRecord | None


@dataclass(frozen=True)
class ApprovalStageResult:
    tool_call: ToolCallRecord | None
    waiting_summary: str | None = None

    @property
    def is_waiting(self) -> bool:
        return self.waiting_summary is not None


class ApprovalRoutingStage:
    def __init__(self, settings: Settings, repository: RunUnitOfWork) -> None:
        self._settings = settings
        self._repository = repository

    async def execute(self, stage_input: ApprovalStageInput) -> ApprovalStageResult:
        disposition = stage_input.authorization.decision.decision
        if disposition == PermissionDecisionKind.deny:
            await self._deny(stage_input)
        if disposition == PermissionDecisionKind.ask:
            return await self._wait_for_approval(stage_input)
        if stage_input.authorization.grant_ids:
            await self._repository.consume_approval_grants(stage_input.authorization.grant_ids)
        return ApprovalStageResult(await self._executable_tool_call(stage_input))

    async def _deny(self, stage_input: ApprovalStageInput) -> None:
        explanation = stage_input.authorization.decision.explanation
        if stage_input.is_approved_resume and stage_input.approved_tool_call:
            await self._repository.finish_tool_call(
                stage_input.approved_tool_call.id,
                error={
                    "category": explanation.reason_code,
                    "message": explanation.summary,
                },
            )
        raise ToolExecutionError(explanation.reason_code, explanation.summary)

    async def _wait_for_approval(
        self,
        stage_input: ApprovalStageInput,
    ) -> ApprovalStageResult:
        explanation = stage_input.authorization.decision.explanation
        if stage_input.is_approved_resume:
            if stage_input.approved_tool_call:
                await self._repository.finish_tool_call(
                    stage_input.approved_tool_call.id,
                    error={
                        "category": "approval_revalidation_required",
                        "message": explanation.summary,
                    },
                )
            raise ToolExecutionError("approval_revalidation_required", explanation.summary)
        tool_call = await self._start_waiting_tool_call(stage_input)
        execution = await self._wait_node_execution(stage_input.active_node_execution_id)
        continuation_token = str(uuid.uuid4())
        request = await self._create_approval_request(
            stage_input,
            tool_call,
            execution,
            continuation_token,
        )
        await self._repository.update_agent_turn(
            stage_input.turn.id,
            status="waiting_user",
            phase="awaiting_approval",
            paused_node="policy_gate",
            tool_call_id=tool_call.id,
        )
        summary = f"等待批准工具调用：{stage_input.tool.spec.name}"
        if execution is not None:
            await self._record_waiting_node(stage_input.run_id, execution)
        await self._repository.set_waiting_state(
            stage_input.run_id,
            {
                "kind": "tool_approval",
                "approval_id": request.id,
                "tool_call_id": tool_call.id,
                "node_execution_id": stage_input.active_node_execution_id,
                "execution_attempt": execution.attempt if execution else None,
                "expected_execution_state_version": (
                    execution.state_version if execution else None
                ),
                "paused_node": "policy_gate",
                "request": summary,
                "continuation_token": continuation_token,
            },
        )
        return ApprovalStageResult(tool_call, summary)

    async def _start_waiting_tool_call(
        self,
        stage_input: ApprovalStageInput,
    ) -> ToolCallRecord:
        tool = stage_input.tool
        return await self._repository.start_tool_call(
            stage_input.run_id,
            stage_input.step.id
            if not stage_input.has_canonical_plan and stage_input.step
            else None,
            tool.spec.name,
            tool.spec.version,
            stage_input.decision.tool_input,
            tool.spec.permission,
            tool.spec.side_effect_level,
            plan_node_id=stage_input.step.id if stage_input.has_canonical_plan else None,
            node_execution_id=stage_input.active_node_execution_id,
            status="awaiting_approval",
        )

    async def _wait_node_execution(self, execution_id: str | None):
        if not execution_id:
            return None
        repository = NodeExecutionRepository(self._repository.session)
        execution = await repository.require(execution_id)
        return await repository.transition(
            execution.id,
            expected_version=execution.state_version,
            phase=NodeExecutionPhase.waiting_approval,
            wait_reason="approval_required",
        )

    async def _create_approval_request(
        self,
        stage_input: ApprovalStageInput,
        tool_call: ToolCallRecord,
        execution,
        continuation_token: str,
    ):
        tool = stage_input.tool
        effect_plan = stage_input.effect_plan
        proposals = grant_proposals(effect_plan)
        return await self._repository.create_approval_request(
            ApprovalRequestCreate(
                run_id=stage_input.run_id,
                turn_id=stage_input.turn.id,
                tool_call_id=tool_call.id,
                tool_name=tool.spec.name,
                tool_version=tool.spec.version,
                frozen_input=stage_input.decision.tool_input,
                input_hash=input_hash(stage_input.decision.tool_input),
                preview=safe_preview(tool.spec.name, stage_input.decision.tool_input),
                permission=", ".join(effect_plan.required_permissions),
                impact=max(
                    (effect.risk for effect in effect_plan.effects),
                    default=tool.spec.side_effect_level,
                ),
                similar_matcher=(
                    proposals[0]
                    if proposals
                    else similar_matcher(tool.spec.name, stage_input.decision.tool_input)
                ),
                frozen_effect_plan=effect_plan.model_dump(mode="json"),
                effect_plan_hash=stage_input.effect_plan_hash,
                analyzer_version=effect_plan.analyzer_version,
                analyzer_digest=effect_plan.analyzer_digest,
                agent_execution_id=stage_input.turn.agent_execution_id,
                continuation_token=continuation_token,
                node_execution_id=stage_input.active_node_execution_id,
                execution_attempt=execution.attempt if execution else None,
                expected_execution_state_version=execution.state_version if execution else None,
            )
        )

    async def _executable_tool_call(
        self,
        stage_input: ApprovalStageInput,
    ) -> ToolCallRecord:
        if stage_input.is_approved_resume:
            assert stage_input.approved_tool_call is not None
            tool_call = await self._repository.transition_tool_call(
                stage_input.approved_tool_call.id,
                "running",
            )
            await self._reacquire_node_slot(tool_call)
            return tool_call
        tool = stage_input.tool
        return await self._repository.start_tool_call(
            stage_input.run_id,
            stage_input.step.id
            if not stage_input.has_canonical_plan and stage_input.step
            else None,
            tool.spec.name,
            tool.spec.version,
            stage_input.decision.tool_input,
            tool.spec.permission,
            tool.spec.side_effect_level,
            plan_node_id=stage_input.step.id if stage_input.has_canonical_plan else None,
            node_execution_id=stage_input.active_node_execution_id,
        )

    async def _reacquire_node_slot(self, tool_call: ToolCallRecord) -> None:
        if not tool_call.node_execution_id:
            return
        repository = NodeExecutionRepository(self._repository.session)
        execution = await repository.require(tool_call.node_execution_id)
        if execution.phase == NodeExecutionPhase.waiting_approval.value:
            await repository.acquire_slot(
                execution.id,
                expected_version=execution.state_version,
                total_slots=self._settings.agent_max_parallel_nodes,
            )

    async def _record_waiting_node(self, run_id: str, execution) -> None:
        await self._repository.add_event(
            run_id,
            "plan.node.waiting_approval",
            {
                "node_execution_id": execution.id,
                "plan_id": execution.plan_id,
                "plan_version": execution.plan_version,
                "plan_node_id": execution.plan_node_id,
                "attempt": execution.attempt,
                "dispatch_batch_id": execution.dispatch_batch_id,
                "slot_index": execution.slot_index,
                "phase": execution.phase,
                "status": execution.status,
                "state_version": execution.state_version,
                "wait_reason": execution.wait_reason,
                "started_at": execution.started_at.isoformat(),
                "heartbeat_at": execution.heartbeat_at.isoformat(),
            },
        )
