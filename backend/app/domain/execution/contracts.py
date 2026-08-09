"""Neutral contracts shared by root-agent and delegated execution runtimes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.common.contracts.json_values import JsonObject
from app.common.schemas.agent.types import ExecutionId, RunId


@dataclass(frozen=True)
class ExecutionLineage:
    run_id: RunId
    execution_id: ExecutionId
    parent_execution_id: ExecutionId | None = None
    depth: int = 0


@dataclass(frozen=True)
class InvocationIntent:
    tool_name: str
    tool_input: JsonObject
    idempotency_key: str
    plan_node_id: str | None
    node_execution_id: str | None


class SubagentSupervisorPort(Protocol):
    parent_execution_id: str

    async def wake(self) -> None: ...

    async def close(self, *, cancel: bool = False) -> None: ...

    async def reconcile(self, *, parent_state_version: int) -> list[JsonObject]: ...

    async def has_pending(self) -> bool: ...

    async def wait(self) -> None: ...
