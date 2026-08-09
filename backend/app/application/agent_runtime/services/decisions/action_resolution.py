"""Resolve one provider decision into a canonical action-boundary value."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from app.application.agent_runtime.contracts import BlockLoop, LoopOutcome
from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.domain.execution.contracts import InvocationIntent


def resolve_action(
    *,
    run_id: str,
    turn_index: int,
    decision: AgentDecision,
    tool_selection: dict[str, Any],
    has_canonical_plan: bool,
    active_plan_node_id: str | None,
    active_plan_node_key: str | None,
    active_node_execution_id: str | None,
) -> InvocationIntent | AgentObservation | LoopOutcome | None:
    allowed_targets = {None, active_plan_node_id, active_plan_node_key}
    if has_canonical_plan and active_plan_node_id and decision.target_step_id not in allowed_targets:
        return AgentObservation(
            kind="decision_error",
            status="failed",
            summary="模型选择了非活动计划节点。",
            data={
                "active_node_id": active_plan_node_id,
                "proposed_node_id": decision.target_step_id,
                "create_turn": False,
            },
        )
    if decision.decision_type != "call_tool":
        return None
    if has_canonical_plan and active_plan_node_id is None:
        return BlockLoop(
            reason="计划没有可执行节点，工具决策已被拒绝。",
            error_code="PLAN_HAS_NO_EXECUTABLE_NODE",
        )
    idempotency_key = hashlib.sha256(
        json.dumps(
            {
                "run_id": run_id,
                "turn_index": turn_index,
                "tool": decision.tool_name,
                "input": decision.tool_input,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    candidate_names = set(tool_selection.get("candidate_names", []))
    if decision.tool_name not in candidate_names:
        return AgentObservation(
            kind="tool_selection_rejected",
            status="failed",
            summary="模型选择的工具不在当前动态候选集中。",
            data={
                "plan_node_id": active_plan_node_id,
                "tool_name": decision.tool_name,
                "candidate_names": sorted(candidate_names),
                "unresolved_capabilities": tool_selection.get("unresolved_capabilities", []),
                "capability_gaps": tool_selection.get("capability_gaps", []),
                "create_turn": True,
                "idempotency_key": idempotency_key,
            },
        )
    return InvocationIntent(
        tool_name=decision.tool_name or "",
        tool_input=dict(decision.tool_input),
        idempotency_key=idempotency_key,
        plan_node_id=active_plan_node_id,
        node_execution_id=active_node_execution_id,
    )
