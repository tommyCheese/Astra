"""Resolve a model decision into a deterministic invocation intent."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.domain.execution.contracts import BlockedOutcome, InvocationIntent


@dataclass(frozen=True)
class ActionResolutionInput:
    run_id: str
    turn_index: int
    decision: AgentDecision
    tool_selection: dict[str, Any]
    has_canonical_plan: bool
    active_plan_node_id: str | None
    active_plan_node_key: str | None
    active_node_execution_id: str | None


@dataclass(frozen=True)
class ResolvedAgentAction:
    invocation: InvocationIntent | None = None
    rejected_observation: AgentObservation | None = None
    terminal_outcome: BlockedOutcome | None = None


class ActionResolutionStage:
    """Enforce plan targeting and the capability-derived tool candidate set."""

    def execute(self, stage_input: ActionResolutionInput) -> ResolvedAgentAction:
        plan_rejection = self._validate_plan_target(stage_input)
        if plan_rejection is not None:
            return plan_rejection
        if stage_input.decision.decision_type != "call_tool":
            return ResolvedAgentAction()
        invocation = InvocationIntent(
            tool_name=stage_input.decision.tool_name or "",
            tool_input=dict(stage_input.decision.tool_input),
            idempotency_key=self._idempotency_key(stage_input),
            plan_node_id=stage_input.active_plan_node_id,
            node_execution_id=stage_input.active_node_execution_id,
        )
        return ResolvedAgentAction(
            invocation=invocation,
            rejected_observation=self._validate_tool_candidate(stage_input),
        )

    @staticmethod
    def _validate_plan_target(
        stage_input: ActionResolutionInput,
    ) -> ResolvedAgentAction | None:
        decision = stage_input.decision
        if not stage_input.has_canonical_plan:
            return None
        allowed_targets = {
            None,
            stage_input.active_plan_node_id,
            stage_input.active_plan_node_key,
        }
        if stage_input.active_plan_node_id and decision.target_step_id not in allowed_targets:
            return ResolvedAgentAction(
                rejected_observation=AgentObservation(
                    kind="decision_error",
                    status="failed",
                    summary="模型选择了非活动计划节点。",
                    data={
                        "active_node_id": stage_input.active_plan_node_id,
                        "proposed_node_id": decision.target_step_id,
                    },
                )
            )
        if decision.decision_type == "call_tool" and stage_input.active_plan_node_id is None:
            return ResolvedAgentAction(
                terminal_outcome=BlockedOutcome(
                    reason="计划没有可执行节点，工具决策已被拒绝。",
                    error_code="PLAN_HAS_NO_EXECUTABLE_NODE",
                )
            )
        return None

    @staticmethod
    def _validate_tool_candidate(
        stage_input: ActionResolutionInput,
    ) -> AgentObservation | None:
        selection = stage_input.tool_selection
        candidate_names = set(selection.get("candidate_names", []))
        if stage_input.decision.tool_name in candidate_names:
            return None
        return AgentObservation(
            kind="tool_selection_rejected",
            status="failed",
            summary="模型选择的工具不在当前动态候选集中。",
            data={
                "plan_node_id": stage_input.active_plan_node_id,
                "tool_name": stage_input.decision.tool_name,
                "candidate_names": sorted(candidate_names),
                "unresolved_capabilities": selection.get("unresolved_capabilities", []),
                "capability_gaps": selection.get("capability_gaps", []),
            },
        )

    @staticmethod
    def _idempotency_key(stage_input: ActionResolutionInput) -> str:
        encoded_intent = json.dumps(
            {
                "run_id": stage_input.run_id,
                "turn_index": stage_input.turn_index,
                "tool": stage_input.decision.tool_name,
                "input": stage_input.decision.tool_input,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(encoded_intent.encode()).hexdigest()
