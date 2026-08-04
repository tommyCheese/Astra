"""Typed model doubles for application and Agent-stage characterization tests."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

from app.model_clients.contracts import AnswerDeltaCallback
from app.model_clients.mock import MockModelClient
from app.schemas.agent.execution_state import AgentDecision
from app.schemas.agent.run_result import FinalAnswer


@dataclass(frozen=True)
class DecisionStep:
    """One deterministic model decision and its optional streamed answer."""

    decision: AgentDecision
    final_answer: FinalAnswer | None = None


class ScriptedDecisionClient(MockModelClient):
    """Replay an explicit decision script while retaining inspected contexts."""

    def __init__(self, steps: list[DecisionStep]) -> None:
        self._remaining_steps = deque(steps)
        self.decision_contexts: list[dict[str, Any]] = []

    async def decide_with_answer(
        self,
        goal: str,
        context: dict[str, Any],
        *,
        on_delta: AnswerDeltaCallback | None = None,
        on_reasoning_delta: AnswerDeltaCallback | None = None,
    ) -> tuple[AgentDecision, FinalAnswer | None]:
        del goal
        self.decision_contexts.append(context)
        if not self._remaining_steps:
            raise AssertionError("The model received more decisions than the test script defines")
        scripted_step = self._remaining_steps.popleft()
        if on_reasoning_delta:
            await on_reasoning_delta(scripted_step.decision.reasoning_summary)
            await on_reasoning_delta("\1")
        if on_delta and scripted_step.final_answer:
            await on_delta(scripted_step.final_answer.summary)
            await on_delta("\1")
        return scripted_step.decision, scripted_step.final_answer

    @property
    def remaining_step_count(self) -> int:
        return len(self._remaining_steps)
