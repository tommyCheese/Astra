"""Normalize tool failures, retry accounting, and reflection."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from app.application.agent_runtime.policies.reasoning import failure_fingerprint
from app.application.agent_runtime.services.shared.progress import (
    ExecutionProgress,
    ProgressEvaluationStage,
)
from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.infrastructure.db.models.runs import AgentTurnRecord
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import ToolExecutionError

logger = logging.getLogger("astra.agent_failure")


@dataclass
class ToolFailureStage:
    repository: RunUnitOfWork
    plugin_runtime: PluginRuntimeState
    tool_registry: Any
    progress: ExecutionProgress
    progress_stage: ProgressEvaluationStage
    retry_counts: dict[str, int] = field(default_factory=dict)
    attempt_counts: dict[str, int] = field(default_factory=dict)

    async def execute(
        self,
        *,
        run_id: str,
        turn_index: int,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        error: ToolExecutionError,
    ) -> None:
        tool_name = decision.tool_name or "unknown"
        action_signature = json.dumps(
            {"tool": decision.tool_name, "input": decision.tool_input},
            sort_keys=True,
            ensure_ascii=False,
        )
        self.attempt_counts[action_signature] = self.attempt_counts.get(action_signature, 0) + 1
        self.retry_counts[tool_name] = self.retry_counts.get(tool_name, 0) + 1
        fingerprint = failure_fingerprint(
            decision.tool_name,
            decision.tool_input,
            error.category,
            decision.reasoning_summary,
        )
        observation = self._observation(decision, error, tool_name, fingerprint)
        self.progress.observations.append(observation.model_dump())
        await self.progress_stage.persist()
        try:
            spec = self.tool_registry.get(tool_name).spec
        except ToolExecutionError:
            spec = None
        self.plugin_runtime.record_failure(
            spec,
            decision.tool_input,
            error.to_payload(),
        )
        reflection = await self.progress_stage.reflect(
            "tool_failed",
            {
                "last_observation": observation.model_dump(),
                "retry_count": self.retry_counts[tool_name],
            },
        )
        await self.repository.update_agent_turn(
            turn.id,
            status="failed",
            observation=observation.model_dump(),
            reflection=reflection.model_dump() if reflection else None,
            reflection_patch=(reflection.patch.model_dump(mode="json") if reflection and reflection.patch else None),
            phase="failed",
        )
        await self.repository.add_event(
            run_id,
            "reasoning.failure_fingerprinted",
            {
                "fingerprint": fingerprint,
                "attempt_count": self.attempt_counts[action_signature],
            },
        )
        await self.repository.session.commit()

    def _observation(
        self,
        decision: AgentDecision,
        error: ToolExecutionError,
        tool_name: str,
        fingerprint: str,
    ) -> AgentObservation:
        failure_data = {
            "tool_name": decision.tool_name,
            "retry_count": self.retry_counts[tool_name],
            "failure_fingerprint": fingerprint,
        }
        return AgentObservation(
            kind="tool_error",
            status="failed",
            summary=f"{decision.tool_name} failed",
            error=error.to_payload(),
            data=failure_data,
        )
