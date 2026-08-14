"""Execute one authorized root-agent tool action and persist its outcome."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Literal

from app.application.agent_runtime.policies.loop import (
    record_progress_signature,
    validate_transition,
)
from app.application.agent_runtime.policies.reasoning import AgentObservationEvaluator
from app.application.agent_runtime.services.completion.memory_candidates import (
    MemoryCandidateWriter,
)
from app.application.agent_runtime.services.shared.progress import (
    ExecutionProgress,
    ProgressEvaluationStage,
)
from app.application.agent_runtime.services.tooling.approval import (
    ApprovalRoutingStage,
    ApprovalStageInput,
)
from app.application.agent_runtime.services.tooling.authorization import (
    AuthorizedInvocation,
    PermissionAuthorizationStage,
    ToolActionInput,
)
from app.application.agent_runtime.services.tooling.failure import ToolFailureStage
from app.application.agent_runtime.services.tooling.invocation import ToolInvocationStage
from app.application.agent_runtime.services.tooling.observation import (
    NormalizedObservation,
    ObservationNormalizationStage,
)
from app.common.schemas.agent.execution_state import NodeResult
from app.common.schemas.agent.planning import ExpectedObservation
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.plans import PlanNodeRecord
from app.infrastructure.db.models.runs import StepRecord
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraToolRegistry, ToolExecutionError

logger = logging.getLogger("astra.agent_runtime.tool_action")

ToolActionOutcome = tuple[
    Literal["continue", "stop"],
    str | None,
    bool,
    bool,
    str | None,
    str | None,
]


@dataclass
class InvocationPipeline:
    """Own the generic authorization-to-observation lifecycle for one invocation."""

    _repository: RunUnitOfWork
    _tool_registry: AstraToolRegistry
    _authorization: PermissionAuthorizationStage
    _approval: ApprovalRoutingStage
    _invocation: ToolInvocationStage
    _observation: ObservationNormalizationStage
    _failure: ToolFailureStage
    _progress: ExecutionProgress
    _progress_stage: ProgressEvaluationStage
    _memory_writer: MemoryCandidateWriter
    _evaluator: AgentObservationEvaluator
    _tool_outputs: list[dict[str, Any]]

    async def execute(self, action: ToolActionInput) -> ToolActionOutcome:
        try:
            return await self._execute_authorized(action)
        except ToolExecutionError as error:
            logger.warning(
                "tool.failed run_id=%s turn=%s tool=%s category=%s",
                action.run_id,
                action.turn_index,
                action.decision.tool_name,
                error.category,
            )
            await self._failure.execute(
                run_id=action.run_id,
                turn_index=action.turn_index,
                turn=action.turn,
                decision=action.decision,
                error=error,
            )
            return "continue", action.workspace_path, False, False, None, None

    async def _execute_authorized(self, action: ToolActionInput) -> ToolActionOutcome:
        self._validate_execution_transition()
        invocation, step, tool_call, waiting_summary = await self._prepare_tool_call(action)
        if waiting_summary is not None:
            return (
                "stop",
                action.workspace_path,
                False,
                False,
                "waiting_user",
                waiting_summary,
            )
        assert tool_call is not None
        self._progress.tool_calls_used += 1
        await self._repository.update_agent_turn(
            action.turn.id,
            phase="executing",
            tool_call_id=tool_call.id,
        )
        result_action, workspace_path, workspace_changed = await self._invoke_and_normalize(
            action,
            invocation,
            tool_call,
            step,
        )
        return (
            result_action,
            workspace_path,
            workspace_changed,
            action.is_approved_resume,
            None,
            None,
        )

    async def _prepare_tool_call(
        self, action: ToolActionInput
    ) -> tuple[
        AuthorizedInvocation,
        PlanNodeRecord | StepRecord | None,
        ToolCallRecord | None,
        str | None,
    ]:
        invocation = await self._authorization.execute(
            action,
            tool_call_count=self._progress.tool_calls_used,
        )
        tool, _, runtime_identity, effect_plan, effect_hash, authorization = invocation
        step = await self._resolve_step(action, tool.spec.name)
        tool_call, waiting_summary = await self._approval.execute(
            ApprovalStageInput(
                run_id=action.run_id,
                turn=action.turn,
                decision=action.decision,
                tool=tool,
                effect_plan=effect_plan,
                effect_plan_hash=effect_hash,
                authorization=authorization,
                step=step,
                active_node_execution_id=action.active_node_execution_id,
                has_canonical_plan=self._progress.active_plan is not None,
                is_approved_resume=action.is_approved_resume,
                approved_tool_call=action.approved_tool_call,
            )
        )
        return invocation, step, tool_call, waiting_summary

    async def _invoke_and_normalize(
        self,
        action: ToolActionInput,
        authorized: AuthorizedInvocation,
        tool_call: ToolCallRecord,
        step: PlanNodeRecord | StepRecord | None,
    ) -> tuple[Literal["continue", "stop"], str | None, bool]:
        tool, _, _, _, _, _ = authorized
        tool_call_id = tool_call.id
        tool_output, workspace_path, workspace_changed = await self._invoke(action, authorized, tool_call, step)
        await self._repository.session.refresh(tool_call)
        if action.active_node is not None:
            await self._repository.session.refresh(action.active_node)
        if step is not None and step is not action.active_node:
            await self._repository.session.refresh(step)
        normalized = await self._normalize(action, authorized, tool_call, tool_output)
        self._tool_outputs.append(normalized.tool_output)
        self._progress.observations.append(normalized.context_observation.model_dump())
        if self._progress.active_plan is None and step is not None:
            await self._repository.update_step(
                step.id,
                "completed",
                evidence=normalized.step_evidence,
            )
        result_action = await self._persist_result(action, authorized, tool_call, normalized)
        logger.info(
            "tool.complete run_id=%s turn=%s tool=%s call_id=%s",
            action.run_id,
            action.turn_index,
            tool.spec.name,
            tool_call_id,
        )
        return result_action, workspace_path, workspace_changed

    async def _invoke(
        self,
        action: ToolActionInput,
        authorized: AuthorizedInvocation,
        tool_call: ToolCallRecord,
        step: PlanNodeRecord | StepRecord | None,
    ):
        tool, _, runtime_identity, effect_plan, _, _ = authorized
        tool_call_id = tool_call.id
        turn_id = action.turn.id
        step_id = step.id if step is not None else None
        runtime_identity_id = runtime_identity.id
        invocation = await self._invocation.execute(
            action,
            tool_call=tool_call,
            step_id=step_id,
            tool=tool,
            effect_plan=effect_plan,
            runtime_identity_id=runtime_identity_id,
        )
        await self._repository.update_agent_turn(
            turn_id,
            phase="result_recorded",
            tool_call_id=tool_call_id,
        )
        return invocation

    async def _normalize(
        self,
        action: ToolActionInput,
        authorized: AuthorizedInvocation,
        tool_call: ToolCallRecord,
        tool_output: dict[str, Any],
    ) -> NormalizedObservation:
        tool, _, runtime_identity, effect_plan, _, _ = authorized
        return await self._observation.execute(
            tool_spec=tool.spec,
            tool_call=tool_call,
            tool_output=tool_output,
            effect_plan=effect_plan,
            runtime_identity_id=runtime_identity.id,
            active_plan_node_id=(
                action.active_node.id if self._progress.active_plan is not None and action.active_node is not None else None
            ),
        )

    async def _persist_result(
        self,
        action: ToolActionInput,
        authorized: AuthorizedInvocation,
        tool_call: ToolCallRecord,
        normalized: NormalizedObservation,
    ) -> Literal["continue", "stop"]:
        tool, _, _, _, _, _ = authorized
        await self._evaluate_and_persist(action, tool_call, normalized)
        return "continue"

    async def _evaluate_and_persist(
        self,
        action: ToolActionInput,
        tool_call: ToolCallRecord,
        normalized: NormalizedObservation,
    ) -> None:
        self._validate_observation_transitions()
        expected, criterion_refs = self._expected_result(action, tool_call.id)
        evaluation = self._evaluator.evaluate(
            normalized.observation,
            expected,
            criterion_refs,
        )
        validate_transition("evaluate", NodeResult(next_node="update_state"))
        await self._repository.add_event(
            action.run_id,
            "reasoning.evaluation_created",
            {"turn_index": action.turn_index, **evaluation.model_dump(mode="json")},
        )
        await self._progress_stage.persist(evaluation)
        validate_transition(
            "update_state",
            NodeResult(next_node="reflection_gate"),
        )
        await self._reflect_on_progress(action, tool_call.id, normalized, evaluation)

    async def _reflect_on_progress(
        self,
        action: ToolActionInput,
        tool_call_id: str,
        normalized: NormalizedObservation,
        evaluation: Any,
    ) -> None:
        evidence_refs = (
            action.active_node.evidence_refs
            if self._progress.active_plan is not None and action.active_node is not None
            else [tool_call_id]
        )
        if record_progress_signature(
            self._progress.no_progress_signatures,
            evidence_refs=evidence_refs,
            criterion_changes=evaluation.criterion_updates,
            completed_steps=[],
            plan_version=self._progress.active_plan.version if self._progress.active_plan is not None else 1,
        ):
            await self._progress_stage.reflect(
                "no_progress",
                {
                    "last_observation": normalized.context_observation.model_dump(),
                    "runtime_context": action.model_context,
                    "retry_count": 0,
                },
            )
        writes = await self._memory_writer.write_candidates(
            run_id=action.run_id,
            goal=action.goal,
            context={
                "run_id": action.run_id,
                "last_observation": normalized.context_observation.model_dump(),
                "evidence_pack": {},
            },
        )
        await self._repository.update_agent_turn(
            action.turn.id,
            status="completed",
            observation=normalized.context_observation.model_dump(),
            tool_call_id=tool_call_id,
            memory_writes=writes,
            evaluation=evaluation.model_dump(mode="json"),
            phase="committed",
        )
        reflection = await self._progress_stage.reflect(
            "turn_completed",
            {
                "last_observation": normalized.context_observation.model_dump(),
                "retry_count": 0,
            },
        )
        if reflection:
            await self._repository.update_agent_turn(
                action.turn.id,
                reflection=reflection.model_dump(),
            )

    async def _resolve_step(
        self,
        action: ToolActionInput,
        tool_name: str,
    ) -> PlanNodeRecord | StepRecord | None:
        if self._progress.active_plan is not None:
            return action.active_node
        run = await self._repository.require_run(action.run_id)
        spec = self._tool_registry.get(tool_name).spec
        keywords = [tool_name, *spec.capabilities]
        for step in sorted(run.steps, key=lambda item: item.index):
            if tool_name in step.intent or tool_name in step.title:
                await self._repository.update_step(step.id, "running")
                return step
            if any(keyword in step.title or keyword in step.intent for keyword in keywords):
                await self._repository.update_step(step.id, "running")
                return step
        step = await self._repository.create_step(
            action.run_id,
            len(run.steps) + 1,
            tool_name,
            f"调用 {tool_name}",
        )
        await self._repository.update_step(step.id, "running")
        return step

    def _expected_result(
        self,
        action: ToolActionInput,
        tool_call_id: str,
    ) -> tuple[ExpectedObservation, list[str]]:
        expected = action.decision.expected
        criterion_refs = action.decision.success_criteria_refs
        if self._progress.active_plan is None or action.active_node is None:
            return expected, criterion_refs
        expected = (
            ExpectedObservation.model_validate(action.active_node.expected_outcome)
            if action.active_node.expected_outcome
            else expected
        )
        criterion_refs = action.active_node.success_criteria_refs or criterion_refs
        action.active_node.evidence_refs = list(dict.fromkeys([*(action.active_node.evidence_refs or []), tool_call_id]))
        return expected, criterion_refs

    def _validate_execution_transition(self) -> None:
        validate_transition(
            "select_action",
            NodeResult(next_node="policy_gate"),
        )
        validate_transition("policy_gate", NodeResult(next_node="execute"))

    def _validate_observation_transitions(self) -> None:
        validate_transition(
            "execute",
            NodeResult(next_node="normalize_observation"),
        )
        validate_transition(
            "normalize_observation",
            NodeResult(next_node="evaluate"),
        )
