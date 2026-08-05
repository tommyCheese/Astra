"""Deterministic completion policy for Run contracts and concurrency barriers."""

from __future__ import annotations

from typing import Any

from app.common.schemas.agent.execution_state import AgentState, CompletionDecision
from app.common.schemas.agent.run_result import AgentValidationOutcome
from app.common.schemas.agent.types import CriterionStatus, TerminalState


class AgentCompletionGate:
    def evaluate_basic(
        self,
        *,
        validation_outcomes: list[AgentValidationOutcome],
        required_user_action: str | None = None,
        runtime_error: str | None = None,
    ) -> CompletionDecision:
        if runtime_error:
            return CompletionDecision(state=TerminalState.failed, reason=runtime_error)
        if required_user_action:
            return CompletionDecision(
                state=TerminalState.waiting_user,
                reason="需要用户输入后才能继续。",
                required_user_action=required_user_action,
            )
        blocking = [
            outcome.validator
            for outcome in validation_outcomes
            if not outcome.passed and outcome.blocking
        ]
        warnings = _completion_warnings([], validation_outcomes)
        if blocking:
            return CompletionDecision(
                state=TerminalState.blocked,
                reason="基础保障存在阻塞问题。",
                unmet_criteria=[f"validator:{validator}" for validator in blocking],
                warnings=warnings,
            )
        return CompletionDecision(
            state=(TerminalState.completed_with_warnings if warnings else TerminalState.completed),
            reason="快速回答已完成基础保障检查。",
            warnings=warnings,
        )

    def evaluate(
        self,
        state: AgentState,
        *,
        validation_outcomes: list[AgentValidationOutcome],
        plan: Any | None = None,
        warnings: list[str] | None = None,
        required_user_action: str | None = None,
        runtime_error: str | None = None,
        active_executions: list[Any] | None = None,
        unresolved_approvals: int = 0,
        unmerged_budgets: int = 0,
        descendant_executions: list[Any] | None = None,
        required_joins: list[Any] | None = None,
    ) -> CompletionDecision:
        barrier_decision = _completion_precondition_decision(
            runtime_error=runtime_error,
            descendants=descendant_executions or [],
            joins=required_joins or [],
            executions=active_executions or [],
            unresolved_approvals=unresolved_approvals,
            unmerged_budgets=unmerged_budgets,
        )
        if barrier_decision:
            return barrier_decision
        waiting_decision = _waiting_user_decision(state, required_user_action)
        if waiting_decision:
            return waiting_decision
        plan_decision = _plan_completion_decision(plan)
        if plan_decision:
            return plan_decision
        combined_warnings = _completion_warnings(warnings or [], validation_outcomes)
        unmet = _unmet_contract_requirements(state, validation_outcomes)
        if not unmet:
            return _successful_completion(combined_warnings)
        return CompletionDecision(
            state=TerminalState.blocked,
            reason="仍有强制成功准则或验证要求未满足。",
            unmet_criteria=unmet,
            warnings=combined_warnings,
        )


def _record_status(item: Any) -> str | None:
    return item.get("status") if isinstance(item, dict) else getattr(item, "status", None)


def _record_id(item: Any, fallback: str = "id") -> str | None:
    if isinstance(item, dict):
        return item.get("id") or item.get(fallback)
    return getattr(item, "id", None)


def _waiting_user_decision(
    state: AgentState, required_user_action: str | None
) -> CompletionDecision | None:
    if not required_user_action and state.task_contract.ambiguity_status == "clear":
        return None
    return CompletionDecision(
        state=TerminalState.waiting_user,
        reason="需要用户输入后才能继续。",
        required_user_action=required_user_action or state.task_contract.clarification_question,
    )


def _successful_completion(warnings: list[str]) -> CompletionDecision:
    terminal_state = TerminalState.completed_with_warnings if warnings else TerminalState.completed
    return CompletionDecision(
        state=terminal_state,
        reason="任务契约与验证要求已满足。",
        warnings=warnings,
    )


def _with_status(items: list[Any], statuses: set[str], *, invert: bool = False) -> list[Any]:
    return [item for item in items if (_record_status(item) in statuses) is not invert]


def _completion_precondition_decision(
    *,
    runtime_error: str | None,
    descendants: list[Any],
    joins: list[Any],
    executions: list[Any],
    unresolved_approvals: int,
    unmerged_budgets: int,
) -> CompletionDecision | None:
    if runtime_error:
        return CompletionDecision(state=TerminalState.failed, reason=runtime_error)
    return _descendant_barrier_decision(descendants, joins) or _execution_barrier_decision(
        executions, unresolved_approvals, unmerged_budgets
    )


def _descendant_barrier_decision(
    descendants: list[Any], joins: list[Any]
) -> CompletionDecision | None:
    terminal = {"completed", "completed_with_warnings", "blocked", "failed", "cancelled"}
    unfinished = _with_status(descendants, terminal, invert=True)
    blocked_joins = _with_status(joins, {"blocked"})
    # A ready Join still has a durable merge result to consume into the parent.
    # Only consumed (or terminally blocked) Joins clear the completion barrier.
    waiting_joins = _with_status(joins, {"consumed", "blocked"}, invert=True)
    if blocked_joins:
        return CompletionDecision(
            state=TerminalState.blocked,
            reason="必需的子 Agent 汇合失败。",
            unmet_criteria=[f"agent-join:{_record_id(item)}" for item in blocked_joins],
        )
    if not unfinished and not waiting_joins:
        return None
    return CompletionDecision(
        state=TerminalState.continue_run,
        reason="子 Agent 终态或必需汇合屏障尚未清空。",
        unmet_criteria=[
            *(f"agent-execution:{_record_id(item)}" for item in unfinished),
            *(f"agent-join:{_record_id(item)}" for item in waiting_joins),
        ],
    )


def _execution_barrier_decision(
    executions: list[Any], unresolved_approvals: int, unmerged_budgets: int
) -> CompletionDecision | None:
    active = [item for item in executions if _record_status(item) in {"active", "waiting"}]
    if not active and not unresolved_approvals and not unmerged_budgets:
        return None
    return CompletionDecision(
        state=TerminalState.continue_run,
        reason="并行执行屏障尚未清空。",
        unmet_criteria=[
            *(f"node-execution:{_record_id(item, 'execution_id')}" for item in active),
            *(["approval:pending"] if unresolved_approvals else []),
            *(["budget:unmerged"] if unmerged_budgets else []),
        ],
    )


def _plan_completion_decision(plan: Any | None) -> CompletionDecision | None:
    required_nodes = [node for node in getattr(plan, "nodes", []) or [] if not node.optional]
    failed = _node_keys_with_status(required_nodes, {"failed", "blocked"})
    if failed:
        return CompletionDecision(
            state=TerminalState.blocked,
            reason="活动计划存在失败或阻塞的必需节点。",
            unmet_criteria=[f"plan-node:{key}" for key in failed],
        )
    unfinished = _node_keys_with_status(required_nodes, {"pending", "running"})
    if not unfinished:
        return None
    return CompletionDecision(
        state=TerminalState.continue_run,
        reason="活动计划仍有未完成的必需节点。",
        unmet_criteria=[f"plan-node:{key}" for key in unfinished],
    )


def _node_keys_with_status(nodes: list[Any], statuses: set[str]) -> list[str]:
    return [node.node_key for node in nodes if node.status.value in statuses]


def _completion_warnings(
    warnings: list[str], validation_outcomes: list[AgentValidationOutcome]
) -> list[str]:
    collected = list(warnings)
    for outcome in validation_outcomes:
        collected.extend(outcome.warnings)
        collected.extend(issue.message for issue in outcome.issues if issue.severity == "warning")
    return list(dict.fromkeys(collected))


def _unmet_contract_requirements(
    state: AgentState, validation_outcomes: list[AgentValidationOutcome]
) -> list[str]:
    unmet = _unmet_success_criteria(state)
    unmet.extend(_unmet_verification_requirements(state, validation_outcomes))
    unmet.extend(_blocking_validator_requirements(validation_outcomes))
    return list(dict.fromkeys(unmet))


def _unmet_success_criteria(state: AgentState) -> list[str]:
    return [
        criterion.id
        for criterion in state.task_contract.success_criteria
        if criterion.mandatory and criterion.status != CriterionStatus.satisfied
    ]


def _unmet_verification_requirements(
    state: AgentState, validation_outcomes: list[AgentValidationOutcome]
) -> list[str]:
    unmet: list[str] = []
    for requirement in state.task_contract.verification_requirements:
        if not requirement.mandatory:
            continue
        matches = [
            outcome
            for outcome in validation_outcomes
            if outcome.validator == requirement.validator or requirement.id in outcome.requirement_ids
        ]
        if not any(outcome.passed for outcome in matches):
            unmet.append(f"verification:{requirement.id}")
    return unmet


def _blocking_validator_requirements(
    validation_outcomes: list[AgentValidationOutcome],
) -> list[str]:
    return [
        f"validator:{outcome.validator}"
        for outcome in validation_outcomes
        if not outcome.passed and outcome.blocking
    ]
