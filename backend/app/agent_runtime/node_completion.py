"""Route finalize and complete-node decisions through completion checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select

from app.agent_runtime.progress import ExecutionProgress, ProgressEvaluationStage
from app.db.models.executions import AgentJoinRecord
from app.db.models.plans import PlanNodeRecord
from app.db.models.runs import AgentTurnRecord
from app.execution.contracts import SubagentSupervisorPort
from app.planning.service import PlanService
from app.repositories.plans import PlanRepository
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.agent.execution_state import AgentDecision, AgentObservation
from app.schemas.agent.run_result import FinalAnswer


@dataclass(frozen=True)
class CompletionRoutingResult:
    action: Literal["not_handled", "continue", "finish"]
    final_turn_id: str | None = None
    streamed_answer: FinalAnswer | None = None
    required_subagent_missing: bool = False
    active_node_completed: bool = False


class NodeCompletionStage:
    def __init__(
        self,
        repository: RunUnitOfWork,
        plan_repository: PlanRepository,
        progress: ExecutionProgress,
        progress_stage: ProgressEvaluationStage,
    ) -> None:
        self._repository = repository
        self._plan_repository = plan_repository
        self._progress = progress
        self._progress_stage = progress_stage

    async def execute(
        self,
        *,
        run_id: str,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        candidate_answer: FinalAnswer | None,
        active_node: PlanNodeRecord | None,
        model_context: dict[str, Any],
        subagent_supervisor: SubagentSupervisorPort | None,
        subagent_mode: str,
        quick_mode: bool,
    ) -> CompletionRoutingResult:
        if decision.decision_type not in {"finalize", "complete_node"}:
            return CompletionRoutingResult("not_handled")
        if await self._reject_unresolved_capabilities(
            run_id,
            turn,
            decision,
            active_node,
            model_context,
        ):
            return CompletionRoutingResult("continue")
        if decision.decision_type == "complete_node":
            return await self._complete_node(run_id, turn, decision, active_node, model_context)
        return await self._finalize(
            run_id,
            turn,
            decision,
            candidate_answer,
            active_node,
            model_context,
            subagent_supervisor,
            subagent_mode,
            quick_mode,
        )

    async def _finalize(
        self,
        run_id: str,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        candidate_answer: FinalAnswer | None,
        active_node: PlanNodeRecord | None,
        model_context: dict[str, Any],
        supervisor: SubagentSupervisorPort | None,
        subagent_mode: str,
        quick_mode: bool,
    ) -> CompletionRoutingResult:
        subagent_result = await self._reconcile_subagents(
            run_id,
            turn,
            supervisor,
            subagent_mode,
        )
        if subagent_result is not None:
            return subagent_result
        if active_node is not None and self._progress.active_plan is not None:
            observation, evaluation, matched = await self._progress_stage.evaluate_node_completion(
                active_node,
                decision,
                candidate_answer,
            )
            if not matched:
                await self._progress_stage.persist_completion_mismatch(
                    turn,
                    observation,
                    evaluation,
                    model_context,
                )
                return CompletionRoutingResult("continue")
            if observation:
                self._progress.observations.append(observation.model_dump(mode="json"))
            if evaluation:
                await self._progress_stage.persist(evaluation)
            await PlanService(self._plan_repository).complete_node(
                run_id,
                active_node.id,
                evaluation=evaluation,
                evidence_refs=self._observation_tool_call_ids(),
            )
            await self._repository.update_agent_turn(
                turn.id,
                status="completed",
                phase="committed",
            )
            await self._repository.session.commit()
            self._progress.active_plan = await self._plan_repository.active_for_run(run_id)
            return CompletionRoutingResult("continue", active_node_completed=True)
        await self._repository.update_agent_turn(turn.id, status="completed")
        return CompletionRoutingResult(
            "finish",
            final_turn_id=turn.id,
            streamed_answer=candidate_answer,
        )

    async def _complete_node(
        self,
        run_id: str,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        active_node: PlanNodeRecord | None,
        model_context: dict[str, Any],
    ) -> CompletionRoutingResult:
        if self._progress.active_plan is None or active_node is None:
            await self._repository.update_agent_turn(turn.id, status="failed", phase="failed")
            await self._repository.add_event(
                run_id,
                "reasoning.decision_rejected",
                {"reason": "complete_node requires an active canonical plan node"},
            )
            await self._repository.session.commit()
            return CompletionRoutingResult("continue")
        observation, evaluation, matched = await self._progress_stage.evaluate_node_completion(
            active_node,
            decision,
        )
        if not matched:
            await self._progress_stage.persist_completion_mismatch(
                turn,
                observation,
                evaluation,
                model_context,
            )
            return CompletionRoutingResult("continue")
        if observation:
            self._progress.observations.append(observation.model_dump(mode="json"))
        if evaluation:
            await self._progress_stage.persist(evaluation)
        await PlanService(self._plan_repository).complete_node(
            run_id,
            active_node.id,
            evaluation=evaluation,
            evidence_refs=active_node.evidence_refs or [],
        )
        await self._repository.update_agent_turn(turn.id, status="completed", phase="committed")
        await self._repository.session.commit()
        return CompletionRoutingResult("continue", active_node_completed=True)

    async def _reject_unresolved_capabilities(
        self,
        run_id: str,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        active_node: PlanNodeRecord | None,
        model_context: dict[str, Any],
    ) -> bool:
        unresolved = list(
            model_context.get("tool_selection", {}).get("unresolved_capabilities", [])
        )
        if active_node is None or not unresolved:
            return False
        observation = AgentObservation(
            kind="capability_requirements_unresolved",
            status="failed",
            summary="活动节点仍有尚未满足的任务能力，不能提前完成。",
            data={
                "plan_node_id": active_node.id,
                "unresolved_capabilities": unresolved,
                "capability_gaps": model_context["tool_selection"].get("capability_gaps", []),
                "candidate_names": model_context["tool_selection"].get("candidate_names", []),
            },
        )
        self._progress.observations.append(observation.model_dump(mode="json"))
        await self._repository.update_agent_turn(
            turn.id,
            status="failed",
            observation=observation.model_dump(mode="json"),
            phase="failed",
        )
        await self._repository.add_event(
            run_id,
            "reasoning.decision_rejected",
            observation.model_dump(mode="json"),
        )
        await self._repository.session.commit()
        return True

    async def _reconcile_subagents(
        self,
        run_id: str,
        turn: AgentTurnRecord,
        supervisor: SubagentSupervisorPort | None,
        subagent_mode: str,
    ) -> CompletionRoutingResult | None:
        if supervisor is None:
            return None
        joins = list(
            (
                await self._repository.session.scalars(
                    select(AgentJoinRecord).where(
                        AgentJoinRecord.parent_execution_id == supervisor.parent_execution_id
                    )
                )
            ).all()
        )
        if subagent_mode == "required" and not joins:
            observation = AgentObservation(
                kind="subagent_required",
                status="failed",
                summary="This Run requires at least one governed Swarm group before completion.",
                data={"required_action": "call_swarm"},
            )
            self._progress.observations.append(observation.model_dump(mode="json"))
            await self._repository.update_agent_turn(
                turn.id,
                status="failed",
                observation=observation.model_dump(mode="json"),
                phase="failed",
            )
            await self._repository.session.commit()
            return CompletionRoutingResult("continue", required_subagent_missing=True)
        if not await supervisor.has_pending():
            return None
        await self._repository.update_agent_turn(
            turn.id,
            status="completed",
            phase="waiting_subagents",
        )
        await self._repository.session.commit()
        await supervisor.wait()
        current = await self._repository.require_run_core(run_id)
        self._progress.observations.extend(
            await supervisor.reconcile(parent_state_version=current.state_version)
        )
        if await supervisor.has_pending():
            self._progress.observations.append(
                AgentObservation(
                    kind="subagent_join",
                    status="waiting",
                    summary="Subagent work or Join reconciliation is still pending.",
                ).model_dump(mode="json")
            )
        return CompletionRoutingResult("continue")

    def _observation_tool_call_ids(self) -> list[str]:
        return [
            str(observation.get("data", {}).get("tool_call_id"))
            for observation in self._progress.observations
            if observation.get("data", {}).get("tool_call_id")
        ]
