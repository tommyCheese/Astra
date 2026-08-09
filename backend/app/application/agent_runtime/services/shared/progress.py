"""Progress, reflection, and plan-revision state for one Agent Run."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.application.agent_runtime.policies.reasoning import (
    AgentObservationEvaluator,
    AgentReflectionGate,
    apply_reflection_patch,
)
from app.application.planning.service import PlanService
from app.common.schemas.agent.execution_state import (
    AgentDecision,
    AgentObservation,
    AgentObservationEvaluation,
    AgentReflection,
    AgentState,
    FailureFingerprint,
    ReflectionPatch,
)
from app.common.schemas.agent.planning import ExpectedObservation
from app.common.schemas.agent.run_policy import EffectiveReasoningPolicy
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.common.schemas.agent.types import EvaluationOutcome
from app.infrastructure.db.models.plans import PlanNodeRecord, PlanRecord
from app.infrastructure.db.models.runs import AgentTurnRecord
from app.infrastructure.model_clients.contracts import ModelClient, ModelOutputError
from app.infrastructure.repositories.plans import PlanRepository, plan_to_view
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraToolRegistry
from app.infrastructure.tools.selection import forbidden_plan_bindings, task_capability_catalog

logger = logging.getLogger("astra.agent_progress")


@dataclass
class ExecutionProgress:
    """Mutable progress deliberately shared by evaluation-related stages."""

    active_plan: PlanRecord | None
    observations: list[dict[str, Any]] = field(default_factory=list)
    tool_calls_used: int = 0
    reflections_used: int = 0
    replans_used: int = 0
    no_progress_signatures: list[str] = field(default_factory=list)


@dataclass
class ProgressEvaluationStage:
    """Own progress persistence, reflection policy, and plan revision."""

    _run_id: str
    _goal: str
    _repository: RunUnitOfWork
    _plan_repository: PlanRepository
    _model_client: ModelClient
    _tool_registry: AstraToolRegistry
    _policy: EffectiveReasoningPolicy
    _reflection_gate: AgentReflectionGate
    _evaluator: AgentObservationEvaluator
    _max_reflections: int
    progress: ExecutionProgress

    async def reflect(
        self,
        signal: str,
        reflection_context: dict[str, Any],
    ) -> AgentReflection | None:
        if not self._may_reflect(signal):
            await self._record_skipped(signal)
            return None
        try:
            reflection = await self._model_client.reflect(self._goal, reflection_context)
        except ModelOutputError as error:
            logger.warning(
                "reflection.invalid_output_skipped run_id=%s signal=%s reason=%s",
                self._run_id,
                signal,
                str(error),
            )
            await self._record_skipped(signal, reason="invalid_model_output")
            return None
        self.progress.reflections_used += 1
        self.progress.observations.append(self._reflection_observation(signal, reflection))
        state_version = await self._apply_reflection_patch(signal, reflection)
        await self._repository.add_event(
            self._run_id,
            "reflection.created",
            {**reflection.model_dump(mode="json"), "state_version": state_version},
        )
        await self._repository.session.commit()
        return reflection

    async def persist(self, evaluation: AgentObservationEvaluation | None = None) -> None:
        current = await self._repository.require_run_core(self._run_id)
        if not current.agent_state:
            return
        state = AgentState.model_validate(current.agent_state)
        state.observations = list(self.progress.observations)
        self._append_failures(state)
        if evaluation is not None:
            self._apply_evaluation(state, evaluation)
        await self._update_budget_usage(state)
        active_plan = await self._active_plan()
        if active_plan:
            state.active_plan_id = active_plan.id
            state.active_plan_version = active_plan.version
        state.version = current.state_version + 1
        await self._repository.update_reasoning_state(
            self._run_id,
            expected_version=current.state_version,
            agent_state=state.model_dump(mode="json"),
            plan_graph=(plan_to_view(active_plan).model_dump(mode="json") if active_plan else current.plan_graph),
            waiting_state=current.waiting_state,
        )

    async def evaluate_node_completion(
        self,
        node: PlanNodeRecord,
        decision: AgentDecision,
        candidate_answer: AgentFinalAnswer | None = None,
    ) -> tuple[AgentObservation | None, AgentObservationEvaluation | None, bool]:
        current = await self._repository.require_run(self._run_id)
        prior_match = next(
            (
                turn.evaluation
                for turn in sorted(current.turns, key=lambda value: value.turn_index, reverse=True)
                if turn.plan_node_id == node.id
                and isinstance(turn.evaluation, dict)
                and turn.evaluation.get("outcome") == EvaluationOutcome.matched.value
            ),
            None,
        )
        if prior_match:
            return None, None, True
        expected = (
            ExpectedObservation.model_validate(node.expected_outcome)
            if node.expected_outcome
            else ExpectedObservation(
                kind="step_result",
                success_condition="node result is available",
            )
        )
        observation_data = dict(decision.node_result or {})
        if candidate_answer is not None:
            observation_data = {**candidate_answer.model_dump(mode="json"), **observation_data}
        observation = AgentObservation(
            kind=expected.kind,
            status="succeeded",
            summary=f"Plan node {node.node_key} proposed completion",
            data=observation_data,
        )
        evaluation = self._evaluator.evaluate(
            observation,
            expected,
            node.success_criteria_refs or [],
        )
        return observation, evaluation, evaluation.outcome == EvaluationOutcome.matched

    async def persist_completion_mismatch(
        self,
        turn: AgentTurnRecord,
        observation: AgentObservation | None,
        evaluation: AgentObservationEvaluation | None,
        model_context: dict[str, Any],
    ) -> None:
        if observation is not None:
            self.progress.observations.append(observation.model_dump(mode="json"))
        if evaluation is not None:
            await self.persist(evaluation)
        await self._repository.update_agent_turn(
            turn.id,
            status="failed",
            observation=observation.model_dump(mode="json") if observation else None,
            evaluation=evaluation.model_dump(mode="json") if evaluation else None,
            phase="failed",
        )
        await self.reflect(
            "expectation_mismatch",
            {
                "last_observation": observation.model_dump(mode="json") if observation else {},
                "runtime_context": model_context,
                "retry_count": 0,
            },
        )

    def _may_reflect(self, signal: str) -> bool:
        return self.progress.reflections_used < self._max_reflections and (
            self._reflection_gate.should_reflect(
                self._policy,
                signal,
                self.progress.reflections_used,
            )
        )

    async def _record_skipped(self, signal: str, *, reason: str | None = None) -> None:
        event = {
            "signal": signal,
            "enabled": self._policy.reflection_enabled,
            "trigger": self._policy.reflection_trigger.value,
            "used": self.progress.reflections_used,
            "limit": self._max_reflections,
        }
        if reason:
            event["reason"] = reason
        await self._repository.add_event(self._run_id, "reflection.skipped", event)
        await self._repository.session.commit()

    async def _apply_reflection_patch(
        self,
        signal: str,
        reflection: AgentReflection,
    ) -> int | None:
        current = await self._repository.require_run_core(self._run_id)
        if not current.agent_state:
            return None
        state = AgentState.model_validate(current.agent_state)
        state.observations = list(self.progress.observations)
        await self._update_budget_usage(state)
        patch = reflection.patch
        if patch and patch.actionable():
            state = await self._apply_actionable_patch(
                signal,
                current.state_version,
                state,
                patch,
            )
        else:
            state.version = current.state_version + 1
        updated = await self._repository.update_reasoning_state(
            self._run_id,
            expected_version=current.state_version,
            agent_state=state.model_dump(mode="json"),
            plan_graph=(
                plan_to_view(self.progress.active_plan).model_dump(mode="json")
                if self.progress.active_plan
                else current.plan_graph
            ),
            waiting_state=current.waiting_state,
        )
        return updated.state_version

    async def _apply_actionable_patch(
        self,
        signal: str,
        expected_version: int,
        state: AgentState,
        patch: ReflectionPatch,
    ) -> AgentState:
        try:
            if patch.plan_patch and self.progress.active_plan is not None:
                tool_specs = self._tool_registry.specs()
                self.progress.active_plan = await PlanService(self._plan_repository).apply_patch(
                    self._run_id,
                    patch.plan_patch,
                    contract=state.task_contract,
                    capabilities=task_capability_catalog(tool_specs),
                    forbidden_capabilities=forbidden_plan_bindings(tool_specs),
                    budgets=self._policy.budgets,
                )
                state.active_plan_id = self.progress.active_plan.id
                state.active_plan_version = self.progress.active_plan.version
                state.active_executions = []
            return apply_reflection_patch(state, patch, expected_version=expected_version)
        except (ValueError, TypeError) as error:
            logger.warning(
                "reflection.patch_rejected run_id=%s signal=%s reason=%s",
                self._run_id,
                signal,
                str(error),
            )
            await self._repository.add_event(
                self._run_id,
                "reflection.patch_rejected",
                {"signal": signal, "reason": str(error)},
            )
            state.version = expected_version + 1
            return state

    async def _update_budget_usage(self, state: AgentState) -> None:
        state.budget_usage.update(
            {
                "turns": await self._repository.count_agent_turns(self._run_id),
                "tool_calls": self.progress.tool_calls_used,
                "reflections": self.progress.reflections_used,
                "replans": self.progress.replans_used,
            }
        )

    async def _active_plan(self) -> PlanRecord | None:
        if self.progress.active_plan is None:
            return None
        active_plan = await self._plan_repository.active_for_run(self._run_id)
        if active_plan:
            self.progress.active_plan = active_plan
        return active_plan

    def _append_failures(self, state: AgentState) -> None:
        known = {failure.fingerprint for failure in state.failures}
        for observation in self.progress.observations:
            fingerprint = observation.get("data", {}).get("failure_fingerprint")
            if fingerprint and fingerprint not in known:
                state.failures.append(
                    FailureFingerprint(
                        fingerprint=fingerprint,
                        tool_name=observation.get("data", {}).get("tool_name"),
                        error_category=(observation.get("error") or {}).get(
                            "category",
                            "unknown",
                        ),
                        attempt_count=int(observation.get("data", {}).get("retry_count", 1)),
                    )
                )
                known.add(fingerprint)

    @staticmethod
    def _apply_evaluation(state: AgentState, evaluation: AgentObservationEvaluation) -> None:
        state.evaluations.append(evaluation.model_dump(mode="json"))
        for criterion in state.task_contract.success_criteria:
            if criterion.id in evaluation.criterion_updates:
                criterion.status = evaluation.criterion_updates[criterion.id]

    @staticmethod
    def _reflection_observation(
        signal: str,
        reflection: AgentReflection,
    ) -> dict[str, Any]:
        return {
            "kind": "reflection",
            "status": "completed",
            "summary": reflection.summary,
            "data": {
                "signal": signal,
                "next_action": reflection.next_action,
                "retry": reflection.retry,
                "revised_tool_input": reflection.revised_tool_input,
            },
        }
