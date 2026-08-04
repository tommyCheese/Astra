"""Resolve and persist one root-agent model decision."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal

from app.agent_runtime.services.action_resolution import (
    ActionResolutionInput,
    ActionResolutionStage,
    ResolvedAction,
)
from app.agent_runtime.services.decision import (
    DecisionStageFailure,
    DecisionStageInput,
    ModelDecisionStage,
)
from app.agent_runtime.services.progress import ExecutionProgress, ProgressEvaluationStage
from app.agent_runtime.services.skill_actions import SkillActionStage
from app.db.models.permissions import AgentIdentityRecord, ToolCallRecord
from app.db.models.plans import PlanNodeRecord
from app.db.models.runs import AgentTurnRecord
from app.model_clients.contracts import ModelClient
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.agent.execution_state import AgentDecision
from app.schemas.agent.run_result import FinalAnswer
from app.schemas.agent.types import AnswerMode

logger = logging.getLogger("astra.agent_runtime.root_decision")

PermissionRuntimeLoader = Callable[[], Awaitable[AgentIdentityRecord]]


@dataclass(frozen=True)
class RootDecisionResult:
    action: Literal["continue", "stop", "ready"]
    turn: AgentTurnRecord | None = None
    decision: AgentDecision | None = None
    candidate_answer: FinalAnswer | None = None
    main_identity: AgentIdentityRecord | None = None
    is_approved_resume: bool = False
    terminal_status: str | None = None
    terminal_summary: str | None = None


class RootDecisionStage:
    """Own model decision, capability resolution, and turn creation."""

    def __init__(
        self,
        *,
        repository: RunUnitOfWork,
        model_client: ModelClient,
        progress: ExecutionProgress,
        progress_stage: ProgressEvaluationStage,
        skill_action_stage: SkillActionStage,
        ensure_permission_runtime: PermissionRuntimeLoader,
        answer_mode: AnswerMode,
        quick_mode: bool,
        on_answer_delta: Callable[[str], Awaitable[None]] | None,
    ) -> None:
        self._repository = repository
        self._model_client = model_client
        self._progress = progress
        self._progress_stage = progress_stage
        self._skills = skill_action_stage
        self._ensure_permissions = ensure_permission_runtime
        self._answer_mode = answer_mode
        self._quick_mode = quick_mode
        self._on_answer_delta = on_answer_delta

    async def execute(
        self,
        *,
        run_id: str,
        goal: str,
        turn_index: int,
        model_context: dict[str, Any],
        active_node: PlanNodeRecord | None,
        active_node_execution_id: str | None,
        approved_tool_call: ToolCallRecord | None,
        approved_turn: AgentTurnRecord | None,
    ) -> RootDecisionResult:
        await self._announce_selection(run_id, turn_index, model_context)
        decision_stage = ModelDecisionStage(
            self._repository,
            self._model_client,
            self._on_answer_delta,
        )
        outcome = await decision_stage.execute(
            DecisionStageInput(
                run_id=run_id,
                goal=goal,
                turn_index=turn_index,
                context=model_context,
                answer_mode=self._answer_mode.value,
                quick_mode=self._quick_mode,
                may_stream_answer=self._progress.active_plan is None or active_node is None,
                active_plan_node_id=active_node.id if active_node is not None else None,
                approved_tool_call=approved_tool_call,
                approved_turn=approved_turn,
            )
        )
        if isinstance(outcome, DecisionStageFailure):
            await self._persist_model_failure(
                run_id,
                turn_index,
                outcome,
                model_context,
                active_node,
                active_node_execution_id,
            )
            return RootDecisionResult("continue")
        identity = await self._ensure_permissions()
        await decision_stage.complete_reasoning(outcome.decision)
        return await self._resolve_and_persist(
            run_id,
            turn_index,
            outcome.decision,
            outcome.candidate_answer,
            identity,
            model_context,
            active_node,
            active_node_execution_id,
            approved_tool_call,
            approved_turn,
        )

    async def _resolve_and_persist(
        self,
        run_id: str,
        turn_index: int,
        decision: AgentDecision,
        candidate_answer: FinalAnswer | None,
        identity: AgentIdentityRecord,
        context: dict[str, Any],
        active_node: PlanNodeRecord | None,
        active_execution_id: str | None,
        approved_call: ToolCallRecord | None,
        approved_turn: AgentTurnRecord | None,
    ) -> RootDecisionResult:
        self._log_decision(run_id, turn_index, decision)
        resolved = self._resolve_action(
            run_id,
            turn_index,
            decision,
            context,
            active_node,
            active_execution_id,
        )
        terminal = self._terminal_resolution(resolved)
        if terminal is not None:
            return terminal
        if resolved.rejected_observation is not None and resolved.invocation is None:
            await self._reject_unresolved_action(run_id, resolved.rejected_observation)
            return RootDecisionResult("continue")
        is_resume = approved_call is not None and approved_turn is not None
        turn = (
            approved_turn
            if is_resume
            else await self._create_turn(
                run_id,
                turn_index,
                decision,
                context,
                active_node,
                active_execution_id,
                resolved.invocation.idempotency_key if resolved.invocation else None,
            )
        )
        assert turn is not None
        if resolved.rejected_observation is not None:
            await self._reject_tool(run_id, turn, resolved.rejected_observation)
            return RootDecisionResult("continue")
        await self._accept_tool_selection(run_id, turn_index, decision, context, active_node)
        if await self._skills.execute(run_id, turn, decision, quick_mode=self._quick_mode):
            return RootDecisionResult("continue")
        await self._record_validated_decision(run_id, turn_index, decision)
        return RootDecisionResult(
            "ready",
            turn=turn,
            decision=decision,
            candidate_answer=candidate_answer,
            main_identity=identity,
            is_approved_resume=is_resume,
        )

    @staticmethod
    def _terminal_resolution(resolved: ResolvedAction) -> RootDecisionResult | None:
        if resolved.terminal_outcome is None:
            return None
        return RootDecisionResult(
            "stop",
            terminal_status="blocked",
            terminal_summary=resolved.terminal_outcome.reason,
        )

    def _resolve_action(
        self,
        run_id: str,
        turn_index: int,
        decision: AgentDecision,
        context: dict[str, Any],
        active_node: PlanNodeRecord | None,
        active_execution_id: str | None,
    ) -> ResolvedAction:
        return ActionResolutionStage().execute(
            ActionResolutionInput(
                run_id=run_id,
                turn_index=turn_index,
                decision=decision,
                tool_selection=context.get("tool_selection", {}),
                has_canonical_plan=self._progress.active_plan is not None,
                active_plan_node_id=active_node.id if active_node is not None else None,
                active_plan_node_key=active_node.node_key if active_node is not None else None,
                active_node_execution_id=active_execution_id,
            )
        )

    async def _persist_model_failure(
        self,
        run_id: str,
        turn_index: int,
        failure: DecisionStageFailure,
        context: dict[str, Any],
        active_node: PlanNodeRecord | None,
        active_execution_id: str | None,
    ) -> None:
        observation = failure.observation
        serialized = observation.model_dump(mode="json")
        self._progress.observations.append(serialized)
        reflection = await self._progress_stage.reflect(
            "model_output_failed",
            {"last_observation": serialized, "retry_count": 0},
        )
        turn = await self._repository.create_agent_turn(
            run_id,
            turn_index,
            "reflect" if reflection else "model_error",
            reflection.summary if reflection else observation.summary,
            decision={"decision_type": "reflect" if reflection else "model_error"},
            memory_reads=context["memory_reads"],
            plan_node_id=active_node.id if active_node is not None else None,
            node_execution_id=active_execution_id,
        )
        await self._repository.update_agent_turn(
            turn.id,
            status="completed",
            observation=serialized,
            reflection=reflection.model_dump(mode="json") if reflection else None,
        )
        await self._repository.session.commit()

    async def _create_turn(
        self,
        run_id: str,
        turn_index: int,
        decision: AgentDecision,
        context: dict[str, Any],
        active_node: PlanNodeRecord | None,
        active_execution_id: str | None,
        idempotency_key: str | None,
    ) -> AgentTurnRecord:
        return await self._repository.create_agent_turn(
            run_id,
            turn_index,
            decision.decision_type,
            decision.reasoning_summary,
            selected_tool=decision.tool_name,
            decision=decision.model_dump(mode="json"),
            memory_reads=context["memory_reads"],
            state_version_before=int(context["state_version"]),
            plan_version=int(context["plan_version"]),
            phase="prepared" if decision.decision_type == "call_tool" else "created",
            idempotency_key=idempotency_key,
            plan_node_id=active_node.id if active_node is not None else None,
            node_execution_id=active_execution_id,
        )

    async def _announce_selection(
        self,
        run_id: str,
        turn_index: int,
        context: dict[str, Any],
    ) -> None:
        if self._quick_mode:
            return
        await self._repository.add_event(
            run_id,
            "tool.resolution.candidates",
            {"turn_index": turn_index, **context["tool_selection"]},
        )
        await self._repository.add_event(
            run_id,
            "reasoning.phase.started",
            {
                "phase": "selecting_action",
                "label": "正在分析下一步",
                "turn_index": turn_index,
            },
        )
        await self._repository.session.commit()

    async def _reject_unresolved_action(self, run_id: str, observation: Any) -> None:
        serialized = observation.model_dump(mode="json")
        self._progress.observations.append(serialized)
        await self._repository.add_event(run_id, "reasoning.decision_rejected", serialized)
        await self._repository.session.commit()

    async def _reject_tool(
        self,
        run_id: str,
        turn: AgentTurnRecord,
        observation: Any,
    ) -> None:
        serialized = observation.model_dump(mode="json")
        self._progress.observations.append(serialized)
        await self._repository.update_agent_turn(
            turn.id,
            status="failed",
            observation=serialized,
        )
        await self._repository.add_event(run_id, "tool.selection.rejected", serialized)
        await self._repository.add_event(run_id, "reasoning.decision_rejected", serialized)
        await self._repository.session.commit()

    async def _accept_tool_selection(
        self,
        run_id: str,
        turn_index: int,
        decision: AgentDecision,
        context: dict[str, Any],
        active_node: PlanNodeRecord | None,
    ) -> None:
        if decision.decision_type != "call_tool":
            return
        await self._repository.add_event(
            run_id,
            "tool.selection.accepted",
            {
                "turn_index": turn_index,
                "plan_node_id": active_node.id if active_node is not None else None,
                "tool_name": decision.tool_name,
                "candidate_names": context.get("tool_selection", {}).get("candidate_names", []),
            },
        )
        await self._repository.session.commit()

    async def _record_validated_decision(
        self,
        run_id: str,
        turn_index: int,
        decision: AgentDecision,
    ) -> None:
        if self._quick_mode:
            return
        await self._repository.add_event(
            run_id,
            "reasoning.decision_validated",
            {
                "turn_index": turn_index,
                "decision_type": decision.decision_type,
                "target_step_id": decision.target_step_id,
            },
        )
        await self._repository.session.commit()

    @staticmethod
    def _log_decision(run_id: str, turn_index: int, decision: AgentDecision) -> None:
        logger.info(
            "agent.decision run_id=%s turn=%s type=%s tool=%s confidence=%.2f",
            run_id,
            turn_index,
            decision.decision_type,
            decision.tool_name,
            decision.confidence,
        )
