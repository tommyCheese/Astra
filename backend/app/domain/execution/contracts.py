"""Neutral contracts shared by root-agent and delegated execution runtimes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, TypeAlias

from app.common.contracts.json_values import JsonObject
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.common.schemas.agent.types import ExecutionId, RunId, TaskId


@dataclass(frozen=True)
class ExecutionLineage:
    run_id: RunId
    execution_id: ExecutionId
    parent_execution_id: ExecutionId | None = None
    depth: int = 0


@dataclass
class ExecutionBudget:
    max_turns: int
    max_tool_calls: int | None
    max_reflections: int
    max_replans: int
    tool_calls_used: int = 0
    reflections_used: int = 0
    replans_used: int = 0


@dataclass
class ExecutionContext:
    run_id: RunId
    task_id: TaskId
    goal: str
    budget: ExecutionBudget
    turn_index: int = 0
    observations: list[JsonObject] = field(default_factory=list)
    tool_outputs: list[JsonObject] = field(default_factory=list)
    retry_counts_by_action: dict[str, int] = field(default_factory=dict)
    failed_attempts_by_action: dict[str, int] = field(default_factory=dict)
    final_turn_id: str | None = None


@dataclass(frozen=True)
class InvocationIntent:
    tool_name: str
    tool_input: JsonObject
    idempotency_key: str
    plan_node_id: str | None
    node_execution_id: str | None


@dataclass(frozen=True)
class ContinueOutcome:
    kind: Literal["continue"] = "continue"


@dataclass(frozen=True)
class WaitingOutcome:
    reason: str
    waiting_state: JsonObject
    kind: Literal["waiting"] = "waiting"


@dataclass(frozen=True)
class CompletedOutcome:
    answer: AgentFinalAnswer
    kind: Literal["completed"] = "completed"


@dataclass(frozen=True)
class BlockedOutcome:
    reason: str
    error_code: str
    kind: Literal["blocked"] = "blocked"


@dataclass(frozen=True)
class FailedOutcome:
    reason: str
    error_code: str
    retryable: bool = False
    kind: Literal["failed"] = "failed"


StageOutcome: TypeAlias = (
    ContinueOutcome | WaitingOutcome | CompletedOutcome | BlockedOutcome | FailedOutcome
)


class SubagentSupervisorPort(Protocol):
    parent_execution_id: str

    async def wake(self) -> None: ...

    async def close(self, *, cancel: bool = False) -> None: ...

    async def reconcile(self, *, parent_state_version: int) -> list[JsonObject]: ...

    async def has_pending(self) -> bool: ...

    async def wait(self) -> None: ...
