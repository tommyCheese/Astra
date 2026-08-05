"""Normalize tool failures, retry accounting, and reflection."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from app.application.agent_runtime.policies.reasoning import failure_fingerprint
from app.application.agent_runtime.result_adapters import ProcessorRegistry
from app.application.agent_runtime.services.progress import (
    ExecutionProgress,
    ProgressEvaluationStage,
)
from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.infrastructure.db.models.runs import AgentTurnRecord
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import ToolExecutionError

logger = logging.getLogger("astra.agent_failure")


@dataclass(frozen=True)
class ToolFailureInput:
    run_id: str
    turn_index: int
    turn: AgentTurnRecord
    decision: AgentDecision
    error: ToolExecutionError
    quick_mode: bool


class ToolFailureStage:
    def __init__(
        self,
        repository: RunUnitOfWork,
        processors: ProcessorRegistry,
        progress: ExecutionProgress,
        progress_stage: ProgressEvaluationStage,
    ) -> None:
        self._repository = repository
        self._processors = processors
        self._progress = progress
        self._progress_stage = progress_stage
        self._retry_counts: dict[str, int] = {}
        self._attempt_counts: dict[str, int] = {}

    async def execute(self, stage_input: ToolFailureInput) -> None:
        decision = stage_input.decision
        tool_name = decision.tool_name or "unknown"
        action_signature = json.dumps(
            {"tool": decision.tool_name, "input": decision.tool_input},
            sort_keys=True,
            ensure_ascii=False,
        )
        self._attempt_counts[action_signature] = self._attempt_counts.get(action_signature, 0) + 1
        self._retry_counts[tool_name] = self._retry_counts.get(tool_name, 0) + 1
        fingerprint = failure_fingerprint(
            decision.tool_name,
            decision.tool_input,
            stage_input.error.category,
            decision.reasoning_summary,
        )
        observation = self._observation(stage_input, tool_name, fingerprint)
        self._progress.observations.append(observation.model_dump())
        await self._progress_stage.persist()
        processor = self._processors.for_tool(tool_name)
        if processor:
            processor.record_failure(tool_name, decision.tool_input, stage_input.error.to_payload())
        reflection = (
            await self._progress_stage.reflect(
                "tool_failed",
                {
                    "last_observation": observation.model_dump(),
                    "retry_count": self._retry_counts[tool_name],
                },
            )
            if not stage_input.quick_mode
            else None
        )
        await self._repository.update_agent_turn(
            stage_input.turn.id,
            status="failed",
            observation=observation.model_dump(),
            reflection=reflection.model_dump() if reflection else None,
            reflection_patch=(
                reflection.patch.model_dump(mode="json")
                if reflection and reflection.patch
                else None
            ),
            phase="failed",
        )
        if not stage_input.quick_mode:
            await self._repository.add_event(
                stage_input.run_id,
                "reasoning.failure_fingerprinted",
                {
                    "fingerprint": fingerprint,
                    "attempt_count": self._attempt_counts[action_signature],
                },
            )
        await self._repository.session.commit()

    def _observation(
        self,
        stage_input: ToolFailureInput,
        tool_name: str,
        fingerprint: str,
    ) -> AgentObservation:
        failure_data = {
            "tool_name": stage_input.decision.tool_name,
            "retry_count": self._retry_counts[tool_name],
            "failure_fingerprint": fingerprint,
        }
        attempted_url = stage_input.decision.tool_input.get("url")
        if (
            stage_input.decision.tool_name == "web_fetch"
            and isinstance(attempted_url, str)
            and attempted_url
        ):
            failure_data["url"] = attempted_url
        return AgentObservation(
            kind="tool_error",
            status="failed",
            summary=f"{stage_input.decision.tool_name} failed",
            error=stage_input.error.to_payload(),
            data=failure_data,
        )
