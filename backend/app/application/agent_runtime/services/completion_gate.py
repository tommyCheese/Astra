"""Evaluate completion criteria against persisted execution state."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select

from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.policies.reasoning import apply_validation_outcomes
from app.application.agent_runtime.services.progress import ExecutionProgress
from app.common.schemas.agent.execution_state import AgentState, CompletionDecision
from app.common.schemas.agent.run_policy import RunExecutionProfile
from app.common.schemas.agent.run_result import AgentAnswerVerificationReport
from app.common.schemas.agent.types import AssuranceLevel, TerminalState
from app.infrastructure.db.models.executions import AgentJoinRecord
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.plans import PlanRepository, plan_to_view
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


@dataclass(frozen=True)
class CompletionGateInput:
    run_id: str
    profile: RunExecutionProfile
    progress: ExecutionProgress
    terminal_status: str | None


class CompletionGateStage:
    def __init__(
        self,
        repository: RunUnitOfWork,
        plan_repository: PlanRepository,
        completion_gate: AgentCompletionGate,
    ) -> None:
        self._repository = repository
        self._plan_repository = plan_repository
        self._completion_gate = completion_gate

    async def evaluate(
        self,
        stage_input: CompletionGateInput,
        verification: AgentAnswerVerificationReport,
    ) -> CompletionDecision:
        run = await self._repository.require_run(stage_input.run_id)
        if not run.agent_state:
            return self._decision_without_agent_state(verification)
        state = AgentState.model_validate(run.agent_state)
        state.observations = list(stage_input.progress.observations)
        state.budget_usage.update(
            turns=len(run.turns),
            tool_calls=stage_input.progress.tool_calls_used,
            reflections=stage_input.progress.reflections_used,
            replans=stage_input.progress.replans_used,
        )
        state = apply_validation_outcomes(state, verification.validation_outcomes)
        state.version = run.state_version + 1
        active_plan = (
            await self._plan_repository.active_for_run(stage_input.run_id)
            if stage_input.progress.active_plan is not None
            else None
        )
        run = await self._repository.update_reasoning_state(
            stage_input.run_id,
            expected_version=run.state_version,
            agent_state=state.model_dump(mode="json"),
            plan_graph=plan_to_view(active_plan).model_dump(mode="json")
            if active_plan
            else run.plan_graph,
            waiting_state=run.waiting_state,
        )
        required_action = (
            (run.waiting_state or {}).get("request")
            if stage_input.terminal_status == "waiting_user"
            else None
        )
        return await self._evaluate_full_or_basic(
            stage_input,
            run,
            state,
            verification,
            required_action,
        )

    async def _evaluate_full_or_basic(
        self,
        stage_input: CompletionGateInput,
        run,
        state: AgentState,
        verification: AgentAnswerVerificationReport,
        required_action: str | None,
    ) -> CompletionDecision:
        if stage_input.profile.assurance_level != AssuranceLevel.full:
            return self._completion_gate.evaluate_basic(
                validation_outcomes=verification.validation_outcomes,
                required_user_action=required_action,
            )
        root = await AgentExecutionRepository(self._repository.session).root_for_run(
            stage_input.run_id
        )
        descendants = (
            await AgentExecutionRepository(self._repository.session).descendants(root.id)
            if root
            else []
        )
        joins = await self._required_joins(root.id) if root else []
        active_plan = (
            await self._plan_repository.active_for_run(stage_input.run_id)
            if stage_input.progress.active_plan is not None
            else None
        )
        return self._completion_gate.evaluate(
            state,
            validation_outcomes=verification.validation_outcomes,
            plan=plan_to_view(active_plan) if active_plan else None,
            required_user_action=required_action,
            active_executions=list(run.node_executions),
            unresolved_approvals=sum(
                approval.status == "pending" for approval in run.approval_requests
            ),
            unmerged_budgets=sum(
                reservation.status == "reserved"
                for execution in run.node_executions
                for reservation in execution.budget_reservations
            ),
            descendant_executions=descendants,
            required_joins=joins,
        )

    async def _required_joins(self, root_execution_id: str) -> list[AgentJoinRecord]:
        joins = (
            await self._repository.session.scalars(
                select(AgentJoinRecord).where(
                    AgentJoinRecord.parent_execution_id == root_execution_id
                )
            )
        ).all()
        return [join for join in joins if join.required_execution_ids]

    @staticmethod
    def _decision_without_agent_state(
        verification: AgentAnswerVerificationReport,
    ) -> CompletionDecision:
        blocking = [
            outcome
            for outcome in verification.validation_outcomes
            if not outcome.passed and outcome.blocking
        ]
        warnings = list(
            dict.fromkeys(
                warning
                for outcome in verification.validation_outcomes
                for warning in outcome.warnings
            )
        )
        return CompletionDecision(
            state=(
                TerminalState.blocked
                if blocking
                else TerminalState.completed_with_warnings
                if warnings
                else TerminalState.completed
            ),
            reason="验证存在阻塞问题。" if blocking else "验证要求已满足。",
            unmet_criteria=[f"validator:{outcome.validator}" for outcome in blocking],
            warnings=warnings,
        )
