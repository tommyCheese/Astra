"""Handle non-tool control decisions from the model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from app.application.agent_runtime.policies.loop import record_progress_signature
from app.application.agent_runtime.services.shared.progress import (
    ExecutionProgress,
    ProgressEvaluationStage,
)
from app.application.planning.scheduler import PlanScheduler
from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.common.schemas.agent.types import PlanNodeStatus
from app.infrastructure.db.models.plans import PlanNodeRecord
from app.infrastructure.db.models.runs import AgentTurnRecord
from app.infrastructure.repositories.plans import PlanRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


@dataclass(frozen=True)
class ControlDecisionResult:
    action: Literal["not_handled", "continue", "stop"]
    terminal_status: str | None = None
    terminal_summary: str | None = None


class ControlDecisionStage:
    """Persist and route blocked, reflection, replanning, and budget decisions."""

    def __init__(
        self,
        repository: RunUnitOfWork,
        plan_repository: PlanRepository,
        scheduler: PlanScheduler,
        progress: ExecutionProgress,
        progress_stage: ProgressEvaluationStage,
        *,
        max_replans: int,
        max_tool_calls: int | None,
    ) -> None:
        self._repository = repository
        self._plan_repository = plan_repository
        self._scheduler = scheduler
        self._progress = progress
        self._progress_stage = progress_stage
        self._max_replans = max_replans
        self._max_tool_calls = max_tool_calls

    async def execute(
        self,
        *,
        run_id: str,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        active_node: PlanNodeRecord | None,
        model_context: dict[str, Any],
    ) -> ControlDecisionResult:
        if decision.decision_type in {"blocked", "ask_user"}:
            return await self._stop_for_model_state(run_id, turn, decision)
        if decision.decision_type == "replan":
            return await self._replan(turn, decision, model_context)
        if decision.decision_type == "reflect":
            return await self._reflect(turn)
        if decision.decision_type != "call_tool":
            return await self._record_non_tool_decision(turn, decision, model_context)
        if self._tool_budget_exhausted():
            return await self._stop_for_tool_budget(run_id, turn, active_node)
        return ControlDecisionResult("not_handled")

    async def _stop_for_model_state(
        self,
        run_id: str,
        turn: AgentTurnRecord,
        decision: AgentDecision,
    ) -> ControlDecisionResult:
        observation = AgentObservation(
            kind="agent_state",
            status=decision.decision_type,
            summary=decision.reasoning_summary,
            data={"required_action": decision.expected_observation},
        )
        serialized = observation.model_dump(mode="json")
        self._progress.observations.append(serialized)
        await self._repository.update_agent_turn(
            turn.id,
            status=decision.decision_type,
            observation=serialized,
        )
        if decision.decision_type == "blocked":
            return ControlDecisionResult(
                "stop",
                terminal_status="blocked",
                terminal_summary=decision.reasoning_summary,
            )
        summary = (decision.expected_observation or "").strip()
        if not summary:
            summary = "请告诉我你希望我完成的具体任务或问题。"
        current_run = await self._repository.require_run_core(run_id)
        await self._repository.set_waiting_state(
            run_id,
            {
                "paused_node": "select_action",
                "state_version": current_run.state_version,
                "plan_version": (current_run.plan_graph or {}).get("version", 1),
                "request": summary,
            },
        )
        return ControlDecisionResult(
            "stop",
            terminal_status="waiting_user",
            terminal_summary=summary,
        )

    async def _replan(
        self,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        model_context: dict[str, Any],
    ) -> ControlDecisionResult:
        self._progress.replans_used += 1
        if self._progress.replans_used > self._max_replans:
            await self._repository.update_agent_turn(turn.id, status="blocked")
            return ControlDecisionResult(
                "stop",
                terminal_status="blocked",
                terminal_summary="已达到用户策略允许的最大重新规划次数。",
            )
        reflection = await self._progress_stage.reflect(
            "dependency_broken",
            {
                "reason": decision.reasoning_summary,
                "last_observation": self._last_observation(),
                "runtime_context": model_context,
                "retry_count": 0,
            },
        )
        await self._repository.update_agent_turn(
            turn.id,
            status="completed" if reflection else "failed",
            reflection=reflection.model_dump(mode="json") if reflection else None,
            reflection_patch=(
                reflection.patch.model_dump(mode="json")
                if reflection and reflection.patch
                else None
            ),
        )
        await self._repository.session.commit()
        return ControlDecisionResult("continue")

    async def _reflect(self, turn: AgentTurnRecord) -> ControlDecisionResult:
        reflection = await self._progress_stage.reflect(
            "model_requested",
            {"last_observation": self._last_observation(), "retry_count": 0},
        )
        await self._repository.update_agent_turn(
            turn.id,
            status="completed",
            reflection=reflection.model_dump(mode="json") if reflection else None,
        )
        return ControlDecisionResult("continue")

    async def _record_non_tool_decision(
        self,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        model_context: dict[str, Any],
    ) -> ControlDecisionResult:
        observation = AgentObservation(
            kind="agent_state",
            status=decision.decision_type,
            summary=decision.reasoning_summary,
        )
        serialized = observation.model_dump(mode="json")
        self._progress.observations.append(serialized)
        if record_progress_signature(
            self._progress.no_progress_signatures,
            evidence_refs=[],
            criterion_changes={},
            completed_steps=[],
            plan_version=self._progress.active_plan.version
            if self._progress.active_plan is not None
            else 1,
        ):
            await self._progress_stage.reflect(
                "no_progress",
                {
                    "last_observation": serialized,
                    "runtime_context": model_context,
                    "retry_count": 0,
                },
            )
        reflection = await self._progress_stage.reflect(
            "turn_completed",
            {"last_observation": serialized, "retry_count": 0},
        )
        await self._repository.update_agent_turn(
            turn.id,
            status="completed",
            observation=serialized,
            reflection=reflection.model_dump(mode="json") if reflection else None,
        )
        return ControlDecisionResult("continue")

    def _tool_budget_exhausted(self) -> bool:
        return (
            self._max_tool_calls is not None
            and self._progress.tool_calls_used >= self._max_tool_calls
        )

    async def _stop_for_tool_budget(
        self,
        run_id: str,
        turn: AgentTurnRecord,
        active_node: PlanNodeRecord | None,
    ) -> ControlDecisionResult:
        observation = AgentObservation(
            kind="limit",
            status="blocked",
            summary="已达到最大工具调用次数。",
            data={"max_tool_calls": self._max_tool_calls},
        )
        serialized = observation.model_dump(mode="json")
        self._progress.observations.append(serialized)
        await self._repository.update_agent_turn(
            turn.id,
            status="blocked",
            observation=serialized,
        )
        if self._progress.active_plan is not None and active_node is not None:
            await self._plan_repository.transition_node(
                active_node.id,
                PlanNodeStatus.blocked,
                failure={"category": "budget_exhausted"},
            )
            await self._scheduler.clear_active_node(run_id, active_node.id)
        return ControlDecisionResult(
            "stop",
            terminal_status="blocked",
            terminal_summary="已达到用户策略允许的最大工具调用次数。",
        )

    def _last_observation(self) -> dict[str, Any]:
        return self._progress.observations[-1] if self._progress.observations else {}
