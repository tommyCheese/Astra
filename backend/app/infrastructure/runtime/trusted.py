"""Trusted profile adapters for the canonical Agent Loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from app.application.agent_runtime.composition import (
    CapabilityRegistration,
    RuntimePorts,
    build_trusted_composition,
    ignore_runtime_event,
    never_cancelled,
)
from app.application.agent_runtime.contracts import (
    ActionProvider,
    BlockLoop,
    CapabilityIdentity,
    CapabilitySlot,
    ContextContribution,
    LoopAction,
    LoopObservation,
    LoopOutcome,
    LoopState,
    ModelDecision,
    SafetyInvariant,
    WaitLoop,
    canonical_observation,
    consume_outcome,
    port_identity,
)
from app.application.agent_runtime.loop import run_loop
from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.policies.reasoning import (
    AgentObservationEvaluator,
    AgentReflectionGate,
)
from app.application.agent_runtime.services.context.turn_preparation import PreparedRootTurn
from app.application.agent_runtime.services.execution.tool_action import ToolActionInput
from app.application.agent_runtime.services.tooling.action_boundary import ActionBoundary
from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_result import AgentFinalAnswer
from app.infrastructure.model_clients.contracts import AnswerDeltaCallback, ModelClient
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.runtime.trusted_factory import TrustedRuntimeFactory
from app.infrastructure.runtime.trusted_state import TrustedRuntime
from app.infrastructure.tools.base import AstraToolRegistry
from app.infrastructure.tools.router import ToolRouter

TRUSTED_STATE = port_identity(
    "trusted-state",
    "b",
    SafetyInvariant.persistence,
    SafetyInvariant.result_unknown_recovery,
)
TRUSTED_CONTEXT = CapabilityIdentity(
    name="trusted-context",
    version=1,
    digest="c" * 64,
    slots=(CapabilitySlot.context,),
    order=0,
)
TRUSTED_MODEL = port_identity("trusted-model", "d")
TRUSTED_DECISION = CapabilityIdentity(
    name="trusted-reasoning-policy",
    version=1,
    digest="e" * 64,
    slots=(CapabilitySlot.decision,),
    order=0,
)
TRUSTED_PROGRESS = CapabilityIdentity(
    name="trusted-progress-reflection",
    version=1,
    digest="f" * 64,
    slots=(CapabilitySlot.progress,),
    order=0,
)
TRUSTED_COMPLETION = CapabilityIdentity(
    name="trusted-verification-evidence-completion-gate",
    version=1,
    digest="0" * 64,
    slots=(CapabilitySlot.completion,),
    order=0,
)
TRUSTED_CANCELLATION = port_identity("trusted-cancellation", "1", SafetyInvariant.cancellation)
TRUSTED_EVENTS = port_identity("trusted-events", "2")


@dataclass
class TrustedRuntimeAdapter:
    runtime: TrustedRuntime
    run_id: str
    goal: str
    prepared: PreparedRootTurn | None = None
    selected_outcome: LoopOutcome | None = None
    finalization: dict[str, Any] | None = None

    def select_terminal(self, status: str | None, summary: str | None) -> LoopOutcome:
        self.runtime.state.terminal_status = status or "blocked"
        self.runtime.state.terminal_summary = summary
        if status == "waiting_user":
            return WaitLoop(reason=summary or "Waiting for user input.")
        return BlockLoop(
            reason=summary or status or "Trusted Runtime stopped.",
            error_code="AGENT_RUNTIME_BLOCKED",
        )

    async def load(self) -> LoopState:
        start_turn = (
            self.runtime.state.approved_turn.turn_index
            if self.runtime.state.approved_turn is not None
            else self.runtime.initial_turn_count
        )
        return LoopState(
            run_id=self.run_id,
            task_id=self.runtime.run.task_id,
            goal=self.goal,
            turn_index=start_turn,
            max_turns=self.runtime.max_turns,
            observations=tuple(canonical_observation(item, status_aliases=TRUSTED_STATUS_ALIASES) for item in self.runtime.progress.observations),
        )

    async def recover(self, state: LoopState) -> tuple[LoopState, LoopOutcome | None]:
        runtime_state = self.runtime.state
        if runtime_state.terminal_status is None:
            return state, None
        return state, self.select_terminal(runtime_state.terminal_status, runtime_state.terminal_summary)

    async def save(self, _state: LoopState, outcome: LoopOutcome) -> None:
        if outcome.kind == "continue" or self.finalization is not None:
            return
        if outcome.kind == "blocked" and self.runtime.state.terminal_status is None:
            self.runtime.state.terminal_status = "blocked"
            self.runtime.state.terminal_summary = outcome.reason
        self.finalization = await self.runtime.finalization_stage.execute(self.runtime, self.goal)

    async def contribute(self, state: LoopState) -> ContextContribution:
        self.prepared = await self.runtime.preparation_stage.execute(run_id=state.run_id, goal=state.goal)
        if self.prepared.terminal_status is not None:
            self.selected_outcome = self.select_terminal(self.prepared.terminal_status, self.prepared.terminal_summary)
        return ContextContribution(
            source="trusted-runtime",
            items=(cast(dict[str, Any], self.prepared.model_context or {}),),
        )

    async def decide(self, state: LoopState, _context: tuple[ContextContribution, ...]) -> ModelDecision:
        if self.selected_outcome is not None:
            return ModelDecision(action=LoopAction(kind="stop", content="prepared terminal"))
        if self.prepared is None or self.prepared.model_context is None:
            raise RuntimeError("trusted context was not prepared")
        runtime_state = self.runtime.state
        return await self.runtime.decision_stage.execute(
            run_id=state.run_id,
            goal=state.goal,
            turn_index=state.turn_index,
            model_context=self.prepared.model_context,
            active_node=self.prepared.active_node,
            active_node_execution_id=self.prepared.active_node_execution_id,
            approved_tool_call=runtime_state.approved_tool_call,
            approved_turn=runtime_state.approved_turn,
        )

    async def apply(
        self,
        state: LoopState,
        _context: tuple[ContextContribution, ...],
        decision: ModelDecision,
    ) -> ModelDecision:
        if self.selected_outcome is not None:
            return decision
        stage = self.runtime.decision_stage
        if stage.outcome is not None:
            self.selected_outcome = stage.outcome
            if stage.outcome.kind == "blocked":
                self.runtime.state.terminal_status = "blocked"
                self.runtime.state.terminal_summary = stage.outcome.reason
            return _routing_decision(decision)
        await self._apply_completion_or_control(state)
        return _routing_decision(decision) if self.selected_outcome is not None else decision

    async def _apply_completion_or_control(self, state: LoopState) -> None:
        stage = self.runtime.decision_stage
        prepared = self.prepared
        assert prepared is not None
        assert stage.turn is not None and stage.decision is not None
        completion = await self.runtime.completion_stage.execute(
            run_id=state.run_id,
            turn=stage.turn,
            decision=stage.decision,
            candidate_answer=stage.candidate_answer,
            active_node=prepared.active_node,
            model_context=cast(dict[str, Any], prepared.model_context),
            subagent_supervisor=self.runtime.subagent_supervisor,
            subagent_mode=self.runtime.profile.subagent_mode,
        )
        if completion is not None and completion.kind == "continue":
            self.runtime.state.required_subagent_missing |= bool(completion.data.get("required_subagent_missing"))
            self.selected_outcome = completion
            return
        if completion is not None and completion.kind == "completed":
            self.runtime.state.required_subagent_missing = False
            self.runtime.state.final_turn_id = cast(str | None, completion.data.get("final_turn_id"))
            answer_value = completion.data.get("streamed_answer")
            self.runtime.state.streamed_final_answer = AgentFinalAnswer.model_validate(answer_value) if answer_value else None
            self.selected_outcome = completion
            return
        control = await self.runtime.control_stage.execute(
            run_id=state.run_id,
            turn=stage.turn,
            decision=stage.decision,
            active_node=prepared.active_node,
            model_context=cast(dict[str, Any], prepared.model_context),
        )
        if control is None:
            return
        if control.kind == "waiting":
            self.runtime.state.terminal_status = "waiting_user"
            self.runtime.state.terminal_summary = control.reason
        elif control.kind == "blocked":
            self.runtime.state.terminal_status = "blocked"
            self.runtime.state.terminal_summary = control.reason
        self.selected_outcome = control

    async def execute(
        self,
        state: LoopState,
        _action: LoopAction,
        _providers: tuple[ActionProvider, ...],
    ) -> LoopObservation:
        prepared = self.prepared
        stage = self.runtime.decision_stage
        assert prepared is not None
        assert stage.turn is not None and stage.decision is not None
        assert stage.main_identity is not None
        (
            action,
            workspace,
            changed,
            clear_resume,
            status,
            summary,
        ) = await self.runtime.tool_stage.execute(
            ToolActionInput(
                run=self.runtime.state.run,
                run_id=state.run_id,
                goal=state.goal,
                turn_index=state.turn_index,
                turn=stage.turn,
                decision=stage.decision,
                main_identity=stage.main_identity,
                active_node=prepared.active_node,
                active_node_execution_id=prepared.active_node_execution_id,
                model_context=cast(dict[str, Any], prepared.model_context),
                execution_mode=self.runtime.execution_mode,
                is_approved_resume=stage.is_approved_resume,
                approved_request_snapshot=self.runtime.state.approved_request_snapshot,
                approved_tool_call=self.runtime.state.approved_tool_call,
                workspace_path=self.runtime.state.workspace_path,
                subagent_supervisor=self.runtime.subagent_supervisor,
            )
        )
        self.runtime.state.workspace_path = workspace
        self.runtime.state.workspace_changed |= changed
        if clear_resume:
            self.runtime.state.approved_tool_call = None
            self.runtime.state.approved_turn = None
            self.runtime.state.approved_request_snapshot = None
        if action == "stop":
            self.selected_outcome = self.select_terminal(status, summary)
            return LoopObservation(kind="system", status="waiting", summary=summary or "")
        if self.runtime.progress.observations:
            return canonical_observation(self.runtime.progress.observations[-1], status_aliases=TRUSTED_STATUS_ALIASES)
        return LoopObservation(kind="tool_result", status="succeeded")

    async def outcome(self, *_args: object) -> LoopOutcome | None:
        self.selected_outcome, outcome = consume_outcome(self.selected_outcome)
        return outcome


async def run_trusted_runtime(
    *,
    settings: AstraRuntimeSettings,
    model_client: ModelClient,
    tool_registry: AstraToolRegistry,
    repository: RunUnitOfWork,
    run_id: str,
    goal: str,
    on_answer_delta: AnswerDeltaCallback | None = None,
    sandbox_provider: Any = None,
    supervisor_close_tasks: set[Any] | None = None,
) -> dict[str, Any]:
    router = ToolRouter(
        tool_registry,
        available_backends={
            "in_process",
            "astra.runtime",
            *({"sandbox.remote"} if settings.sandbox_enabled else set()),
        },
    )
    runtime = await TrustedRuntimeFactory(
        settings,
        model_client,
        tool_registry,
        router,
        PluginRuntimeState.from_registry(tool_registry),
        AgentObservationEvaluator(),
        AgentReflectionGate(),
        AgentCompletionGate(),
        sandbox_provider,
        supervisor_close_tasks or set(),
        _normalize_tool_output,
    ).build(
        repository=repository,
        run_id=run_id,
        goal=goal,
        on_answer_delta=on_answer_delta,
    )
    await _record_runtime_limits(repository, run_id, runtime)
    adapter = TrustedRuntimeAdapter(runtime, run_id, goal)
    ports = RuntimePorts(
        identities=(
            TRUSTED_MODEL,
            TRUSTED_STATE,
            ActionBoundary.identity,
            TRUSTED_CANCELLATION,
            TRUSTED_EVENTS,
        ),
        model=adapter.decide,
        load=adapter.load,
        recover=adapter.recover,
        save=adapter.save,
        action=adapter.execute,
        cancellation=never_cancelled,
        event=ignore_runtime_event,
    )
    await run_loop(
        build_trusted_composition(
            ports=ports,
            registrations=(
                CapabilityRegistration(TRUSTED_CONTEXT, adapter.contribute),
                CapabilityRegistration(TRUSTED_DECISION, adapter.apply),
                CapabilityRegistration(TRUSTED_PROGRESS, adapter.outcome),
                CapabilityRegistration(TRUSTED_COMPLETION, adapter.outcome),
            ),
        )
    )
    if adapter.finalization is None:
        raise RuntimeError("trusted Runtime did not finalize")
    return adapter.finalization


def _routing_decision(decision: ModelDecision) -> ModelDecision:
    return ModelDecision(
        action=LoopAction(kind="stop", content="handled by trusted policy"),
        reasoning_summary=decision.reasoning_summary,
    )


TRUSTED_STATUS_ALIASES = {
    "success": "succeeded",
    "completed": "succeeded",
    "denied": "rejected",
    "blocked": "rejected",
}


def _normalize_tool_output(tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
    return {**output, "tool_name": tool_name}


async def _record_runtime_limits(
    repository: RunUnitOfWork,
    run_id: str,
    runtime: TrustedRuntime,
) -> None:
    await repository.add_event(
        run_id,
        "reasoning.runtime_limits",
        {
            "reasoning_effort": runtime.policy.reasoning_effort.value,
            "max_turns": runtime.max_turns,
            "max_tool_calls": runtime.max_tool_calls,
            "max_reflections": runtime.max_reflections,
            "max_replans": runtime.max_replans,
        },
    )
    await repository.session.commit()
