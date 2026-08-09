from __future__ import annotations

from enum import Enum
from typing import NewType

RunId = NewType("RunId", str)
TaskId = NewType("TaskId", str)
ExecutionId = NewType("ExecutionId", str)


class ReasoningEffort(str, Enum):
    fast = "fast"
    balanced = "balanced"
    deep = "deep"


TOOL_CALL_LIMIT_RANGES: dict[ReasoningEffort, tuple[int, int]] = {
    ReasoningEffort.fast: (0, 5),
    ReasoningEffort.balanced: (6, 15),
}

TOOL_CALL_LIMIT_DEFAULTS: dict[ReasoningEffort, int | None] = {
    ReasoningEffort.fast: 5,
    ReasoningEffort.balanced: 8,
    ReasoningEffort.deep: None,
}


def validate_tool_call_limit(effort: ReasoningEffort, value: int) -> int:
    if effort == ReasoningEffort.deep:
        raise ValueError("max_tool_calls must be unlimited for deep reasoning")
    minimum, maximum = TOOL_CALL_LIMIT_RANGES[effort]
    if not minimum <= value <= maximum:
        raise ValueError(f"max_tool_calls must be between {minimum} and {maximum} for {effort.value} reasoning")
    return value


class PlanStatus(str, Enum):
    planned = "planned"
    active = "active"
    superseded = "superseded"
    completed = "completed"


class PlanNodeStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"
    skipped = "skipped"


class NodeExecutionPhase(str, Enum):
    claimed = "claimed"
    running = "running"
    waiting_resource = "waiting_resource"
    waiting_approval = "waiting_approval"
    committing = "committing"
    cancelling = "cancelling"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    result_unknown = "result_unknown"


class NodeExecutionStatus(str, Enum):
    active = "active"
    waiting = "waiting"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    blocked = "blocked"


class ReflectionTrigger(str, Enum):
    failure_only = "failure_only"
    adaptive = "adaptive"
    every_turn = "every_turn"


class ExecutionMode(str, Enum):
    request_approval = "request_approval"
    auto_approval = "auto_approval"


class PlanExecution(str, Enum):
    auto = "auto"
    confirm = "confirm"


class ContinuationAction(str, Enum):
    execute_plan = "execute_plan"
    revise_plan = "revise_plan"


class VerificationLevel(str, Enum):
    basic = "basic"
    standard = "standard"
    strict = "strict"


class AnswerMode(str, Enum):
    standard = "standard"
    trusted = "trusted"


class RuntimeKind(str, Enum):
    fast_v1 = "fast-v1"
    trusted_v1 = "trusted-v1"


class AssuranceLevel(str, Enum):
    basic = "basic"
    full = "full"


class ContractMode(str, Enum):
    system_minimal = "system_minimal"
    model = "model"


class CriterionStatus(str, Enum):
    pending = "pending"
    satisfied = "satisfied"
    failed = "failed"
    waived = "waived"


class EvaluationOutcome(str, Enum):
    matched = "matched"
    partial = "partial"
    mismatch = "mismatch"
    conflict = "conflict"
    inconclusive = "inconclusive"


class TerminalState(str, Enum):
    continue_run = "continue"
    completed = "completed"
    completed_with_warnings = "completed_with_warnings"
    waiting_user = "waiting_user"
    blocked = "blocked"
    failed = "failed"


class ApprovalDecision(str, Enum):
    approve_once = "approve_once"
    allow_similar = "allow_similar"
    allow_task = "allow_task"
    reject = "reject"
