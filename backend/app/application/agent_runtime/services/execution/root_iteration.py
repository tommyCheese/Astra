"""One readable root-agent iteration driven by typed stage outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from app.application.agent_runtime.services.completion.node_completion import NodeCompletionStage
from app.application.agent_runtime.services.context.turn_preparation import (
    PreparedRootTurn,
    RootTurnPreparationStage,
)
from app.application.agent_runtime.services.decisions.control import ControlDecisionStage
from app.application.agent_runtime.services.decisions.root import (
    RootDecisionResult,
    RootDecisionStage,
)
from app.application.agent_runtime.services.execution.tool_action import (
    InvocationPipeline,
    ToolActionInput,
)
from app.common.schemas.agent.execution_state import AgentDecision
from app.common.schemas.agent.run_policy import RunExecutionProfile
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.domain.execution.contracts import (
    BlockedOutcome,
    CompletedOutcome,
    ContinueOutcome,
    ExecutionContext,
    StageOutcome,
    SubagentSupervisorPort,
    WaitingOutcome,
)
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import AgentTurnRecord, RunRecord


@dataclass
class RootRuntimeState:
    """Mutable state owned by the root runtime, outside read-only stage inputs."""

    run: RunRecord
    profile: RunExecutionProfile
    approved_tool_call: ToolCallRecord | None = None
    approved_turn: AgentTurnRecord | None = None
    approved_request_snapshot: dict | None = None
    workspace_path: str | None = None
    workspace_changed: bool = False
    required_subagent_missing: bool = False
    final_turn_id: str | None = None
    streamed_final_answer: AgentFinalAnswer | None = None
    terminal_status: str | None = None
    terminal_summary: str | None = None


class RootAgentIterationStage:
    """Compose preparation, decision, completion, control, and invocation stages."""

    def __init__(
        self,
        *,
        state: RootRuntimeState,
        preparation_stage: RootTurnPreparationStage,
        decision_stage: RootDecisionStage,
        completion_stage: NodeCompletionStage,
        control_stage: ControlDecisionStage,
        tool_stage: InvocationPipeline,
        subagent_supervisor: SubagentSupervisorPort | None,
        execution_mode: str,
    ) -> None:
        self._state = state
        self._preparation = preparation_stage
        self._decisions = decision_stage
        self._completion = completion_stage
        self._control = control_stage
        self._tools = tool_stage
        self._subagents = subagent_supervisor
        self._execution_mode = execution_mode

    async def execute(self, context: ExecutionContext) -> StageOutcome:
        if self._state.terminal_status is not None:
            return self._terminal_outcome()
        prepared = await self._preparation.execute(
            run_id=context.run_id,
            goal=context.goal,
        )
        if prepared.terminal_status is not None:
            return self._stop(prepared.terminal_status, prepared.terminal_summary)
        decision = await self._decisions.execute(
            run_id=context.run_id,
            goal=context.goal,
            turn_index=context.turn_index,
            model_context=self._require_context(prepared),
            active_node=prepared.active_node,
            active_node_execution_id=prepared.active_node_execution_id,
            approved_tool_call=self._state.approved_tool_call,
            approved_turn=self._state.approved_turn,
        )
        if decision.action == "continue":
            return ContinueOutcome()
        if decision.action == "stop":
            return self._stop(decision.terminal_status, decision.terminal_summary)
        return await self._route_ready_decision(context, prepared, decision)

    async def _route_ready_decision(
        self,
        context: ExecutionContext,
        prepared: PreparedRootTurn,
        resolved: RootDecisionResult,
    ) -> StageOutcome:
        turn = resolved.turn
        decision = resolved.decision
        identity = resolved.main_identity
        model_context = self._require_context(prepared)
        assert turn is not None and decision is not None and identity is not None
        completion = await self._completion.execute(
            run_id=context.run_id,
            turn=turn,
            decision=decision,
            candidate_answer=resolved.candidate_answer,
            active_node=prepared.active_node,
            model_context=model_context,
            subagent_supervisor=self._subagents,
            subagent_mode=self._state.profile.subagent_mode,
        )
        if completion.action == "continue":
            self._state.required_subagent_missing |= completion.required_subagent_missing
            return ContinueOutcome()
        if completion.action == "finish":
            self._state.required_subagent_missing = False
            self._state.final_turn_id = completion.final_turn_id
            self._state.streamed_final_answer = completion.streamed_answer
            return CompletedOutcome(answer=completion.streamed_answer or AgentFinalAnswer(summary=""))
        return await self._route_control_or_tool(
            context,
            prepared,
            resolved,
            turn,
            decision,
            model_context,
        )

    async def _route_control_or_tool(
        self,
        context: ExecutionContext,
        prepared: PreparedRootTurn,
        resolved: RootDecisionResult,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        model_context: dict,
    ) -> StageOutcome:
        control = await self._control.execute(
            run_id=context.run_id,
            turn=turn,
            decision=decision,
            active_node=prepared.active_node,
            model_context=model_context,
        )
        if control.action == "continue":
            return ContinueOutcome()
        if control.action == "stop":
            return self._stop(control.terminal_status, control.terminal_summary)
        return await self._invoke_tool(context, prepared, resolved, turn, decision, model_context)

    async def _invoke_tool(
        self,
        context: ExecutionContext,
        prepared: PreparedRootTurn,
        resolved: RootDecisionResult,
        turn: AgentTurnRecord,
        decision: AgentDecision,
        model_context: dict,
    ) -> StageOutcome:
        assert resolved.main_identity is not None
        (
            action,
            workspace_path,
            workspace_changed,
            clear_approved_resume,
            terminal_status,
            terminal_summary,
        ) = await self._tools.execute(
            ToolActionInput(
                run=self._state.run,
                run_id=context.run_id,
                goal=context.goal,
                turn_index=context.turn_index,
                turn=turn,
                decision=decision,
                main_identity=resolved.main_identity,
                active_node=prepared.active_node,
                active_node_execution_id=prepared.active_node_execution_id,
                model_context=model_context,
                execution_mode=self._execution_mode,
                is_approved_resume=resolved.is_approved_resume,
                approved_request_snapshot=self._state.approved_request_snapshot,
                approved_tool_call=self._state.approved_tool_call,
                workspace_path=self._state.workspace_path,
                subagent_supervisor=self._subagents,
            )
        )
        self._state.workspace_path = workspace_path
        self._state.workspace_changed |= workspace_changed
        if clear_approved_resume:
            self._clear_approved_resume()
        if action == "stop":
            if terminal_status is None:
                return CompletedOutcome(answer=AgentFinalAnswer(summary=""))
            return self._stop(terminal_status, terminal_summary)
        return ContinueOutcome()

    def _stop(self, status: str | None, summary: str | None) -> StageOutcome:
        self._state.terminal_status = status or "blocked"
        self._state.terminal_summary = summary
        return self._terminal_outcome()

    def _terminal_outcome(self) -> StageOutcome:
        summary = self._state.terminal_summary or self._state.terminal_status or ""
        if self._state.terminal_status == "waiting_user":
            return WaitingOutcome(reason=summary, waiting_state={})
        return BlockedOutcome(reason=summary, error_code="AGENT_RUNTIME_BLOCKED")

    def _clear_approved_resume(self) -> None:
        self._state.approved_tool_call = None
        self._state.approved_turn = None
        self._state.approved_request_snapshot = None

    @staticmethod
    def _require_context(prepared: PreparedRootTurn) -> dict:
        assert prepared.model_context is not None
        return prepared.model_context
