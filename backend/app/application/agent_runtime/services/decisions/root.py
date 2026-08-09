"""Resolve and persist one root-agent model decision."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, cast

from app.application.agent_runtime.contracts import (
    ContinueLoop,
    LoopAction,
    LoopOutcome,
    ModelDecision,
)
from app.application.agent_runtime.services.decisions.action_resolution import resolve_action
from app.application.agent_runtime.services.decisions.model import ModelDecisionStage
from app.application.agent_runtime.services.decisions.skills import SkillActionStage
from app.application.agent_runtime.services.shared.progress import (
    ExecutionProgress,
    ProgressEvaluationStage,
)
from app.common.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.common.schemas.agent.types import AnswerMode
from app.domain.execution.contracts import InvocationIntent
from app.infrastructure.db.models.permissions import AgentIdentityRecord, ToolCallRecord
from app.infrastructure.db.models.plans import PlanNodeRecord
from app.infrastructure.db.models.runs import AgentTurnRecord
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

logger = logging.getLogger("astra.agent_runtime.root_decision")

PermissionRuntimeLoader = Callable[[], Awaitable[AgentIdentityRecord]]


@dataclass
class RootDecisionStage:
    """Own model decision, capability resolution, and turn creation."""

    _repository: RunUnitOfWork
    _model_client: ModelClient
    _progress: ExecutionProgress
    _progress_stage: ProgressEvaluationStage
    _skills: SkillActionStage
    _ensure_permissions: PermissionRuntimeLoader
    _answer_mode: AnswerMode
    _on_answer_delta: Callable[[str], Awaitable[None]] | None
    turn: AgentTurnRecord | None = None
    decision: AgentDecision | None = None
    candidate_answer: AgentFinalAnswer | None = None
    main_identity: AgentIdentityRecord | None = None
    is_approved_resume: bool = False
    outcome: LoopOutcome | None = None

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
    ) -> ModelDecision:
        self.outcome = None
        self.turn = None
        self.decision = None
        self.candidate_answer = None
        self.main_identity = None
        self.is_approved_resume = False
        await self._announce_selection(run_id, turn_index, model_context)
        decision_stage = ModelDecisionStage(
            self._repository,
            self._model_client,
            self._on_answer_delta,
        )
        outcome = await decision_stage.execute(
            run_id=run_id,
            goal=goal,
            turn_index=turn_index,
            context=model_context,
            answer_mode=self._answer_mode.value,
            may_stream_answer=self._progress.active_plan is None or active_node is None,
            active_plan_node_id=active_node.id if active_node is not None else None,
            approved_tool_call=approved_tool_call,
            approved_turn=approved_turn,
        )
        if isinstance(outcome, AgentObservation):
            await self._persist_model_failure(
                run_id,
                turn_index,
                outcome,
                model_context,
                active_node,
                active_node_execution_id,
            )
            self.outcome = ContinueLoop()
            return _handled_decision("model failure handled")
        identity = await self._ensure_permissions()
        await decision_stage.complete_reasoning(outcome)
        return await self._resolve_and_persist(
            run_id,
            turn_index,
            outcome,
            decision_stage.candidate_answer,
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
        candidate_answer: AgentFinalAnswer | None,
        identity: AgentIdentityRecord,
        context: dict[str, Any],
        active_node: PlanNodeRecord | None,
        active_execution_id: str | None,
        approved_call: ToolCallRecord | None,
        approved_turn: AgentTurnRecord | None,
    ) -> ModelDecision:
        self._log_decision(run_id, turn_index, decision)
        resolved = resolve_action(
            run_id=run_id,
            turn_index=turn_index,
            decision=decision,
            tool_selection=context.get("tool_selection", {}),
            has_canonical_plan=self._progress.active_plan is not None,
            active_plan_node_id=active_node.id if active_node is not None else None,
            active_plan_node_key=active_node.node_key if active_node is not None else None,
            active_node_execution_id=active_execution_id,
        )
        if isinstance(resolved, LoopOutcome) and resolved.kind == "blocked":
            self.outcome = resolved
            return _handled_decision(resolved.reason)
        if isinstance(resolved, AgentObservation) and not resolved.data.get("create_turn"):
            await self._reject_unresolved_action(run_id, resolved)
            self.outcome = ContinueLoop()
            return _handled_decision("unresolved action rejected")
        invocation = resolved if isinstance(resolved, InvocationIntent) else None
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
                invocation.idempotency_key
                if invocation
                else cast(str | None, resolved.data.get("idempotency_key"))
                if isinstance(resolved, AgentObservation)
                else None,
            )
        )
        assert turn is not None
        if isinstance(resolved, AgentObservation):
            await self._reject_tool(run_id, turn, resolved)
            self.outcome = ContinueLoop()
            return _handled_decision("tool selection rejected")
        await self._accept_tool_selection(run_id, turn_index, decision, context, active_node)
        if await self._skills.execute(run_id, turn, decision):
            self.outcome = ContinueLoop()
            return _handled_decision("Skill action completed")
        await self._record_validated_decision(run_id, turn_index, decision)
        self.turn = turn
        self.decision = decision
        self.candidate_answer = candidate_answer
        self.main_identity = identity
        self.is_approved_resume = is_resume
        return _canonical_decision(decision)

    async def _persist_model_failure(
        self,
        run_id: str,
        turn_index: int,
        failure: AgentObservation,
        context: dict[str, Any],
        active_node: PlanNodeRecord | None,
        active_execution_id: str | None,
    ) -> None:
        observation = failure
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


def _canonical_decision(decision: AgentDecision) -> ModelDecision:
    if decision.decision_type == "call_tool":
        action = LoopAction(
            kind="tool",
            name=decision.tool_name,
            input=decision.tool_input,
            reason=decision.reasoning_summary,
        )
    elif decision.decision_type == "ask_user":
        action = LoopAction(
            kind="ask_user",
            content=decision.expected_observation or decision.reasoning_summary,
        )
    else:
        action = LoopAction(kind="stop", content=decision.reasoning_summary or decision.decision_type)
    return ModelDecision(action=action, reasoning_summary=decision.reasoning_summary)


def _handled_decision(reason: str) -> ModelDecision:
    return ModelDecision(action=LoopAction(kind="stop", content=reason))
