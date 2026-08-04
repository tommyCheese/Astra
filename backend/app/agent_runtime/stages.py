"""Narrow callable contract implemented by every Agent execution stage."""

from __future__ import annotations

from typing import Protocol

from app.execution.contracts import ExecutionContext, StageOutcome


class ExecutionStage(Protocol):
    async def execute(self, context: ExecutionContext) -> StageOutcome: ...
