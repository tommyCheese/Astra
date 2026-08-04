"""Deterministic routing of typed Agent execution stages."""

from __future__ import annotations

from collections.abc import Sequence
from typing import assert_never

from app.agent_runtime.stages import ExecutionStage
from app.execution.contracts import (
    BlockedOutcome,
    CompletedOutcome,
    ContinueOutcome,
    ExecutionContext,
    FailedOutcome,
    StageOutcome,
    WaitingOutcome,
)


class AgentRunOrchestrator:
    """Expose stage order, turn budget, and exhaustive outcome routing only."""

    def __init__(self, stages: Sequence[ExecutionStage]) -> None:
        self._stages = tuple(stages)

    async def execute(self, context: ExecutionContext) -> StageOutcome:
        for turn_index in range(context.turn_index, context.budget.max_turns):
            context.turn_index = turn_index + 1
            outcome = await self._execute_iteration(context)
            routed = self._route(outcome)
            if not isinstance(routed, ContinueOutcome):
                return routed
        return BlockedOutcome(
            reason="Agent turn budget exhausted before a terminal outcome.",
            error_code="TURN_BUDGET_EXHAUSTED",
        )

    async def _execute_iteration(self, context: ExecutionContext) -> StageOutcome:
        for stage in self._stages:
            outcome = await stage.execute(context)
            if not isinstance(outcome, ContinueOutcome):
                return outcome
        return ContinueOutcome()

    @staticmethod
    def _route(outcome: StageOutcome) -> StageOutcome:
        match outcome:
            case ContinueOutcome():
                return outcome
            case WaitingOutcome():
                return outcome
            case CompletedOutcome():
                return outcome
            case BlockedOutcome():
                return outcome
            case FailedOutcome():
                return outcome
        assert_never(outcome)
