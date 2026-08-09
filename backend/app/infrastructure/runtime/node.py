"""Bounded Plan-node profile running on the canonical Agent Loop."""

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
    CompleteLoop,
    ContextContribution,
    ContinueLoop,
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
from app.application.agent_runtime.services.tooling.action_boundary import ActionBoundary
from app.application.planning.node_worker import ReadOnlyAgentNodeExecutor
from app.common.schemas.agent.types import NodeExecutionStatus
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


def build_node_executor(settings: Any, model_client: Any, tool_registry: Any) -> ReadOnlyAgentNodeExecutor:
    return ReadOnlyAgentNodeExecutor(
        settings,
        model_client=model_client,
        tool_registry=tool_registry,
        runtime_runner=run_node_runtime,
    )

NODE_STATE = port_identity(
    "node-state",
    "3",
    SafetyInvariant.persistence,
    SafetyInvariant.result_unknown_recovery,
)
NODE_CONTEXT = CapabilityIdentity(
    name="ready-node-input",
    version=1,
    digest="4" * 64,
    slots=(CapabilitySlot.context,),
    order=0,
)
NODE_MODEL = port_identity("node-model", "5")
NODE_DECISION = CapabilityIdentity(
    name="node-completion-policy",
    version=1,
    digest="6" * 64,
    slots=(CapabilitySlot.decision,),
    order=0,
)
NODE_PROGRESS = CapabilityIdentity(
    name="node-progress",
    version=1,
    digest="7" * 64,
    slots=(CapabilitySlot.progress,),
    order=0,
)
NODE_COMPLETION = CapabilityIdentity(
    name="node-terminal",
    version=1,
    digest="8" * 64,
    slots=(CapabilitySlot.completion,),
    order=0,
)
NODE_CANCELLATION = port_identity("node-cancellation", "9", SafetyInvariant.cancellation)
NODE_EVENTS = port_identity("node-events", "0")


@dataclass
class NodeRuntimeAdapter:
    executor: ReadOnlyAgentNodeExecutor
    repository: RunUnitOfWork
    context: Any
    runtime: Any
    resolution: Any = None
    allowed_tools: Any = None
    decision: Any = None
    candidate_answer: Any = None
    turn: Any = None
    selected_outcome: LoopOutcome | None = None
    result: Any = None

    def budget(self, turn: int) -> dict[str, int]:
        return {
            "turns": turn,
            "tool_calls": self.runtime.tool_calls,
            "model_calls": turn,
        }

    async def load(self) -> LoopState:
        return LoopState(
            run_id=self.context.run_id,
            task_id=self.runtime.run.task_id,
            goal=self.runtime.goal,
            max_turns=self.runtime.maximum_turns,
            observations=tuple(canonical_observation(value, status_aliases=NODE_STATUS_ALIASES) for value in self.runtime.observations),
        )

    async def recover(self, state: LoopState) -> tuple[LoopState, LoopOutcome | None]:
        return state, None

    async def save(self, state: LoopState, outcome: LoopOutcome) -> None:
        if outcome.kind == "continue" or self.result is not None:
            return
        self.result = _node_result(
            self.context,
            status=NodeExecutionStatus.failed,
            evidence_refs=self.runtime.evidence_refs,
            observations=self.runtime.observations,
            budget=self.budget(state.turn_index),
            failure={"category": _failure_category(outcome)},
        )

    async def contribute(self, _state: LoopState) -> ContextContribution:
        return ContextContribution(source="plan-node", items=(cast(dict[str, Any], self.context.node),))

    async def decide(self, state: LoopState, _context: tuple[ContextContribution, ...]) -> ModelDecision:
        prepared = await self.executor.prepare_turn(self.repository, self.context, self.runtime, state.turn_index)
        (
            self.resolution,
            self.allowed_tools,
            self.decision,
            self.candidate_answer,
            self.turn,
        ) = prepared
        decision = self.decision
        action = (
            LoopAction(
                kind="tool",
                name=decision.tool_name,
                input=decision.tool_input,
                reason=decision.reasoning_summary,
            )
            if decision.decision_type == "call_tool" and decision.tool_name
            else LoopAction(kind="stop", content=decision.reasoning_summary or "node control")
        )
        return ModelDecision(action=action, reasoning_summary=decision.reasoning_summary)

    async def apply(
        self,
        state: LoopState,
        _context: tuple[ContextContribution, ...],
        decision: ModelDecision,
    ) -> ModelDecision:
        selected = self.decision
        if selected.decision_type in {"complete_node", "finalize"}:
            self.result = await self.executor.complete_node(
                self.repository,
                self.context,
                self.runtime,
                state.turn_index,
                self.turn,
                self.resolution,
                selected,
                self.candidate_answer,
            )
            self.selected_outcome = (
                CompleteLoop(answer=selected.reasoning_summary) if self.result is not None else ContinueLoop()
            )
            return _handled(decision)
        if selected.decision_type != "call_tool" or not selected.tool_name:
            await self.repository.update_agent_turn(
                self.turn.id,
                status="failed",
                phase="failed",
                observation={
                    "kind": "decision_error",
                    "status": "failed",
                    "summary": "Parallel node requires a tool call or node completion.",
                },
            )
            self.selected_outcome = ContinueLoop()
            return _handled(decision)
        if self.runtime.tool_calls >= self.runtime.maximum_tool_calls:
            self.result = _node_result(
                self.context,
                status=NodeExecutionStatus.failed,
                evidence_refs=self.runtime.evidence_refs,
                observations=self.runtime.observations,
                budget=self.budget(state.turn_index),
                failure={"category": "node_tool_budget_exhausted"},
            )
            self.selected_outcome = BlockLoop(
                reason="Node tool budget exhausted.",
                error_code="NODE_TOOL_BUDGET_EXHAUSTED",
            )
            return _handled(decision)
        return decision

    async def execute(
        self,
        state: LoopState,
        _action: LoopAction,
        _providers: tuple[ActionProvider, ...],
    ) -> LoopObservation:
        tool = await self.executor.select_tool(
            self.repository,
            self.context,
            self.runtime,
            state.turn_index,
            self.turn,
            self.resolution,
            self.allowed_tools,
            self.decision,
        )
        if tool is None:
            return _last_observation(self.runtime)
        prepared = await self.executor.prepare_tool_execution(
            self.repository,
            self.context,
            self.runtime,
            state.turn_index,
            self.turn,
            tool,
            self.decision,
        )
        if prepared.early_result is not None:
            self.result = prepared.early_result
            status = prepared.early_result.status
            self.selected_outcome = (
                WaitLoop(reason="Node is waiting for a resource lease.")
                if status == NodeExecutionStatus.waiting
                else BlockLoop(
                    reason="Node requires serial execution.",
                    error_code="NODE_REQUIRES_SERIAL_EXECUTION",
                )
            )
            return LoopObservation(
                kind="node_execution",
                status="waiting" if status == NodeExecutionStatus.waiting else "rejected",
                summary=str(prepared.early_result.failure or status.value),
            )
        await self.executor.invoke_tool(
            self.repository,
            self.context,
            self.runtime,
            self.turn,
            tool,
            self.decision,
            prepared,
        )
        return _last_observation(self.runtime)

    async def outcome(self, *_args: object) -> LoopOutcome | None:
        self.selected_outcome, outcome = consume_outcome(self.selected_outcome)
        return outcome


async def run_node_runtime(executor: ReadOnlyAgentNodeExecutor, repository: RunUnitOfWork, context: Any) -> Any:
    from app.application.planning.node_runtime import prepare_parallel_node_runtime

    runtime = await prepare_parallel_node_runtime(executor.settings, repository, context)
    adapter = NodeRuntimeAdapter(executor, repository, context, runtime)
    ports = RuntimePorts(
        identities=(
            NODE_MODEL,
            NODE_STATE,
            ActionBoundary.identity,
            NODE_CANCELLATION,
            NODE_EVENTS,
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
                CapabilityRegistration(NODE_CONTEXT, adapter.contribute),
                CapabilityRegistration(NODE_DECISION, adapter.apply),
                CapabilityRegistration(NODE_PROGRESS, adapter.outcome),
                CapabilityRegistration(NODE_COMPLETION, adapter.outcome),
            ),
        )
    )
    if adapter.result is None:
        raise RuntimeError("node Runtime did not produce a result")
    return adapter.result


def _handled(decision: ModelDecision) -> ModelDecision:
    return ModelDecision(
        action=LoopAction(kind="stop", content="handled by node policy"),
        reasoning_summary=decision.reasoning_summary,
    )


def _last_observation(runtime: Any) -> LoopObservation:
    if not runtime.observations:
        return LoopObservation(kind="tool_result", status="failed")
    return canonical_observation(runtime.observations[-1], status_aliases=NODE_STATUS_ALIASES)


NODE_STATUS_ALIASES = {"success": "succeeded", "completed": "succeeded", "blocked": "rejected"}


def _node_result(context: Any, **values: Any) -> Any:
    from app.application.planning.coordinator import NodeExecutionResult

    return NodeExecutionResult(
        execution_id=context.execution_id,
        plan_node_id=context.plan_node_id,
        plan_version=context.plan_version,
        attempt=context.attempt,
        status=values["status"],
        evidence_refs=values["evidence_refs"],
        observations=values["observations"],
        budget_consumed=values["budget"],
        failure=values["failure"],
    )


def _failure_category(outcome: LoopOutcome) -> str:
    if outcome.kind == "blocked" and outcome.error_code == "TURN_BUDGET_EXHAUSTED":
        return "node_turn_budget_exhausted"
    return f"node_{outcome.kind}"
