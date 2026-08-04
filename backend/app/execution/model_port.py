"""Narrow model-provider port required by delegated execution."""

from __future__ import annotations

from typing import Protocol

from app.contracts.json_values import JsonObject, JsonValue
from app.schemas.agent.execution_state import AgentDecision, AgentReflection
from app.schemas.agent.planning import PlanDraft, TaskContract
from app.schemas.agent.run_result import FinalAnswer


class DelegatedModelPort(Protocol):
    def bind_agent_execution(self, agent_execution_id: str | None) -> None: ...

    async def plan(self, goal: str, *, contract: TaskContract) -> PlanDraft: ...

    async def decide_with_answer(
        self,
        goal: str,
        context: JsonObject,
        **kwargs: JsonValue,
    ) -> tuple[AgentDecision, FinalAnswer | None]: ...

    async def reflect(self, goal: str, context: JsonObject) -> AgentReflection: ...
