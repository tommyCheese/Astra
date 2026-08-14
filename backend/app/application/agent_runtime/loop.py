"""The fixed, capability-composed Agent iteration loop."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar, cast

from pydantic import TypeAdapter

from app.application.agent_runtime.composition import RuntimeComposition
from app.application.agent_runtime.contracts import (
    BlockLoop,
    CancelLoop,
    CapabilitySlot,
    CompleteLoop,
    ContextContribution,
    ContinueLoop,
    FailLoop,
    LoopObservation,
    LoopOutcome,
    LoopState,
    ModelDecision,
    RuntimeEvent,
    WaitLoop,
)

Contribution = TypeVar("Contribution")
_OUTCOME_ADAPTER = TypeAdapter(LoopOutcome)
logger = logging.getLogger("astra.agent_runtime.loop")


class LoopContractError(ValueError):
    """A capability violated its bounded contribution contract."""


async def run_loop(composition: RuntimeComposition) -> LoopOutcome:
    """Run one canonical state machine until it reaches a typed outcome."""
    state = await composition.ports.load()
    try:
        await _publish(composition, state, RuntimeEvent(name="loop.started"))
        state, outcome = await composition.ports.recover(state)
        if outcome is None:
            outcome, state = await _iterate(composition, state)
    except LoopContractError as exc:
        outcome = FailLoop(
            reason=str(exc),
            error_code="RUNTIME_CONTRACT_VIOLATION",
        )
    except Exception as exc:  # adapters classify provider details at their boundary
        logger.exception("runtime.loop.failed run_id=%s", state.run_id)
        outcome = FailLoop(
            reason=str(exc),
            error_code="RUNTIME_FAILURE",
        )
    await composition.ports.save(state, outcome)
    await _publish(
        composition,
        state,
        RuntimeEvent(name="loop.finished", payload={"outcome": outcome.kind}),
        outcome,
    )
    return outcome


async def _iterate(composition: RuntimeComposition, state: LoopState) -> tuple[LoopOutcome, LoopState]:
    while state.turn_index < state.max_turns:
        try:
            if await composition.ports.cancellation(state.run_id):
                return CancelLoop(), state
            state = state.model_copy(update={"turn_index": state.turn_index + 1})
            await _publish(
                composition,
                state,
                RuntimeEvent(name="turn.started", payload={"turn": state.turn_index}),
            )
            outcome, state = await _run_turn(composition, state)
        except LoopContractError as exc:
            return FailLoop(reason=str(exc), error_code="RUNTIME_CONTRACT_VIOLATION"), state
        except Exception as exc:  # adapters classify provider details at their boundary
            logger.exception("runtime.turn.failed run_id=%s turn=%s", state.run_id, state.turn_index)
            return FailLoop(reason=str(exc), error_code="RUNTIME_FAILURE"), state
        if outcome.kind != "continue":
            return outcome, state
        await composition.ports.save(state, outcome)
    return (
        BlockLoop(
            reason="Agent turn budget exhausted before a terminal outcome.",
            error_code="TURN_BUDGET_EXHAUSTED",
        ),
        state,
    )


async def _run_turn(composition: RuntimeComposition, state: LoopState) -> tuple[LoopOutcome, LoopState]:
    context = await _collect_context(composition, state)
    decision = await composition.ports.model(state.model_copy(deep=True), context)
    decision = ModelDecision.model_validate(decision)
    decision = await _apply_decision_policies(composition, state, context, decision)
    await _publish(
        composition,
        state,
        RuntimeEvent(
            name="decision.selected",
            payload={
                "turn": state.turn_index,
                "kind": decision.action.kind,
                "name": decision.action.name,
            },
        ),
    )
    observation = await _execute_action(composition, state, decision)
    if observation is not None:
        observation = await _process_observation(composition, state, observation)
        state = state.model_copy(update={"observations": (*state.observations, observation)})
        await _publish(
            composition,
            state,
            RuntimeEvent(name="observation.recorded", payload={"kind": observation.kind}),
        )
    outcome = await _select_outcome(composition, state, decision, observation)
    return outcome, state


async def _collect_context(composition: RuntimeComposition, state: LoopState) -> tuple[ContextContribution, ...]:
    contributions: list[ContextContribution] = []
    providers = cast(tuple, composition.providers(CapabilitySlot.context))
    for provider in providers:
        result = await _bounded_call(state, provider)
        contributions.append(ContextContribution.model_validate(result))
    return tuple(contributions)


async def _apply_decision_policies(
    composition: RuntimeComposition,
    state: LoopState,
    context: tuple[ContextContribution, ...],
    decision: ModelDecision,
) -> ModelDecision:
    policies = cast(tuple, composition.providers(CapabilitySlot.decision))
    for policy in policies:
        result = await _bounded_call(
            state,
            lambda isolated, policy=policy, decision=decision: policy(isolated, context, decision),
        )
        decision = ModelDecision.model_validate(result)
    return decision


async def _execute_action(
    composition: RuntimeComposition,
    state: LoopState,
    decision: ModelDecision,
) -> LoopObservation | None:
    if decision.action.kind != "tool":
        return None
    providers = cast(tuple, composition.providers(CapabilitySlot.action))
    result = await composition.ports.action(state.model_copy(deep=True), decision.action, providers)
    return LoopObservation.model_validate(result)


async def _process_observation(
    composition: RuntimeComposition,
    state: LoopState,
    observation: LoopObservation,
) -> LoopObservation:
    processors = cast(
        tuple,
        composition.providers(CapabilitySlot.observation),
    )
    for processor in processors:
        result = await _bounded_call(
            state,
            lambda isolated, processor=processor, observation=observation: processor(isolated, observation),
        )
        observation = LoopObservation.model_validate(result)
    return observation


async def _select_outcome(
    composition: RuntimeComposition,
    state: LoopState,
    decision: ModelDecision,
    observation: LoopObservation | None,
) -> LoopOutcome:
    if observation is not None:
        policies = cast(tuple, composition.providers(CapabilitySlot.progress))
        for policy in policies:
            selected = await _bounded_call(
                state,
                lambda isolated, policy=policy: policy(isolated, observation),
            )
            if selected is not None:
                return _OUTCOME_ADAPTER.validate_python(selected)
    completion = cast(tuple, composition.providers(CapabilitySlot.completion))
    for policy in completion:
        selected = await _bounded_call(
            state,
            lambda isolated, policy=policy: policy(isolated, decision, observation),
        )
        if selected is not None:
            return _OUTCOME_ADAPTER.validate_python(selected)
    action = decision.action
    if action.kind == "tool":
        return ContinueLoop()
    if action.kind == "answer":
        return CompleteLoop(answer=action.content or "")
    if action.kind == "ask_user":
        return WaitLoop(reason=action.content or "")
    return BlockLoop(reason=action.content or "Model stopped.", error_code="MODEL_STOPPED")


async def _publish(
    composition: RuntimeComposition,
    state: LoopState,
    event: RuntimeEvent,
    outcome: LoopOutcome | None = None,
) -> None:
    await composition.ports.event(event)
    observers = cast(tuple, composition.providers(CapabilitySlot.lifecycle))
    for observer in observers:
        await _bounded_call(
            state,
            lambda isolated, observer=observer: observer(isolated, event, outcome),
        )


async def _bounded_call(
    state: LoopState,
    operation: Callable[[LoopState], Awaitable[Contribution]],
) -> Contribution:
    isolated = state.model_copy(deep=True)
    before = isolated.model_dump_json()
    result = await operation(isolated)
    if isolated.model_dump_json() != before:
        raise LoopContractError("capability attempted to mutate canonical Loop state")
    return result
