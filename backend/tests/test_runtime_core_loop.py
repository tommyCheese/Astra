from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.application.agent_runtime.composition import (
    CapabilityRegistration,
    RuntimePorts,
    build_standard_composition,
)
from app.application.agent_runtime.contracts import (
    ActionProvider,
    BlockLoop,
    CapabilityIdentity,
    CapabilitySlot,
    CompleteLoop,
    ContextContribution,
    FailLoop,
    LoopAction,
    LoopObservation,
    LoopOutcome,
    LoopState,
    ModelDecision,
    PortIdentity,
    RuntimeEvent,
    SafetyInvariant,
    WaitLoop,
)
from app.application.agent_runtime.loop import run_loop


def port_identity(name: str, digest_character: str, *coverage: SafetyInvariant) -> PortIdentity:
    return PortIdentity(
        name=name,
        version=1,
        digest=digest_character * 64,
        safety_coverage=frozenset(coverage),
    )


def capability_identity(name: str, slot: CapabilitySlot, order: int = 0):
    return CapabilityIdentity(
        name=name,
        version=1,
        digest="a" * 64,
        slots=(slot,),
        order=order,
    )


@dataclass
class FakeStatePort:
    state: LoopState
    identity: PortIdentity = field(
        default_factory=lambda: port_identity(
            "state",
            "1",
            SafetyInvariant.persistence,
            SafetyInvariant.result_unknown_recovery,
        )
    )
    saves: list[tuple[LoopState, LoopOutcome]] = field(default_factory=list)

    async def load(self) -> LoopState:
        return self.state

    async def recover(self, state: LoopState) -> tuple[LoopState, LoopOutcome | None]:
        return state, None

    async def save(self, state: LoopState, outcome: LoopOutcome) -> None:
        self.state = state
        self.saves.append((state, outcome))


@dataclass
class FakeModelPort:
    decisions: list[ModelDecision]
    identity: PortIdentity = field(default_factory=lambda: port_identity("model", "0"))

    async def decide(self, _state, _context) -> ModelDecision:
        return self.decisions.pop(0)


@dataclass
class FakeActionPort:
    observations: list[LoopObservation]
    identity: PortIdentity = field(
        default_factory=lambda: port_identity(
            "action",
            "2",
            SafetyInvariant.schema_validation,
            SafetyInvariant.effect_analysis,
            SafetyInvariant.authorization,
            SafetyInvariant.approval_integrity,
        )
    )
    calls: list[LoopAction] = field(default_factory=list)
    failure: Exception | None = None

    async def execute(
        self,
        _state: LoopState,
        action: LoopAction,
        _providers: tuple[ActionProvider, ...],
    ) -> LoopObservation:
        self.calls.append(action)
        if self.failure:
            raise self.failure
        return self.observations.pop(0)


@dataclass
class FakeCancellationPort:
    cancelled: bool = False
    identity: PortIdentity = field(default_factory=lambda: port_identity("cancellation", "3", SafetyInvariant.cancellation))

    async def is_cancelled(self, _run_id: str) -> bool:
        return self.cancelled


@dataclass
class FakeEventPort:
    identity: PortIdentity = field(default_factory=lambda: port_identity("event", "4"))
    events: list[RuntimeEvent] = field(default_factory=list)

    async def publish(self, event: RuntimeEvent) -> None:
        self.events.append(event)


@dataclass
class ProgressCapability:
    outcome: LoopOutcome | None
    identity: CapabilityIdentity = field(default_factory=lambda: capability_identity("progress", CapabilitySlot.progress))

    async def evaluate(self, _state, _observation):
        return self.outcome


@dataclass
class ObservationCapability:
    identity: CapabilityIdentity = field(default_factory=lambda: capability_identity("observation", CapabilitySlot.observation))

    async def process(self, _state, observation):
        return observation.model_copy(update={"summary": "normalized"})


@dataclass
class MutatingContextCapability:
    identity: CapabilityIdentity = field(default_factory=lambda: capability_identity("mutator", CapabilitySlot.context))

    async def contribute(self, state: LoopState) -> ContextContribution:
        state.extension_state["forbidden"] = True
        return ContextContribution(source="mutator")


def registration(capability) -> CapabilityRegistration:
    slot = capability.identity.slots[0]
    method = {
        CapabilitySlot.context: "contribute",
        CapabilitySlot.observation: "process",
        CapabilitySlot.progress: "evaluate",
    }[slot]
    return CapabilityRegistration(capability.identity, getattr(capability, method))


def harness(
    decisions: list[ModelDecision],
    *,
    max_turns: int = 3,
    observations: list[LoopObservation] | None = None,
    registrations: tuple[CapabilityRegistration, ...] = (),
    cancelled: bool = False,
):
    state = FakeStatePort(
        LoopState(
            run_id="run-1",
            task_id="task-1",
            goal="test the minimal loop",
            max_turns=max_turns,
        )
    )
    model = FakeModelPort(decisions)
    action = FakeActionPort(observations or [])
    cancellation = FakeCancellationPort(cancelled)
    events = FakeEventPort()
    composition = build_standard_composition(
        ports=RuntimePorts(
            identities=(
                model.identity,
                state.identity,
                action.identity,
                cancellation.identity,
                events.identity,
            ),
            model=model.decide,
            load=state.load,
            recover=state.recover,
            save=state.save,
            action=action.execute,
            cancellation=cancellation.is_cancelled,
            event=events.publish,
        ),
        registrations=registrations,
    )
    return composition, state, action, events


def answer(text: str) -> ModelDecision:
    return ModelDecision(action=LoopAction(kind="answer", content=text))


def tool() -> ModelDecision:
    return ModelDecision(action=LoopAction(kind="tool", name="workspace_read", input={"path": "a"}))


@pytest.mark.asyncio
async def test_answer_completes_through_one_loop() -> None:
    composition, state, action, events = harness([answer("done")])

    outcome = await run_loop(composition)

    assert outcome == CompleteLoop(answer="done")
    assert action.calls == []
    assert [saved.kind for _, saved in state.saves] == ["completed"]
    assert [event.name for event in events.events] == [
        "loop.started",
        "turn.started",
        "decision.selected",
        "loop.finished",
    ]


@pytest.mark.asyncio
async def test_tool_observation_continues_then_answer_completes() -> None:
    composition, state, action, _events = harness(
        [tool(), answer("done")],
        observations=[LoopObservation(kind="tool", status="succeeded")],
        registrations=(registration(ObservationCapability()),),
    )

    outcome = await run_loop(composition)

    assert outcome.kind == "completed"
    assert [saved.kind for _, saved in state.saves] == ["continue", "completed"]
    assert state.state.turn_index == 2
    assert state.state.observations[0].summary == "normalized"
    assert len(action.calls) == 1


@pytest.mark.asyncio
async def test_progress_policy_can_wait_after_an_observation() -> None:
    waiting = WaitLoop(reason="approval required", state={"approval_id": "a-1"})
    composition, state, _action, _events = harness(
        [tool()],
        observations=[LoopObservation(kind="approval", status="waiting")],
        registrations=(registration(ProgressCapability(waiting)),),
    )

    outcome = await run_loop(composition)

    assert outcome == waiting
    assert [saved.kind for _, saved in state.saves] == ["waiting"]


@pytest.mark.asyncio
async def test_cancellation_stops_before_a_model_decision() -> None:
    composition, state, action, events = harness([], cancelled=True)

    outcome = await run_loop(composition)

    assert outcome.kind == "cancelled"
    assert action.calls == []
    assert state.state.turn_index == 0
    assert [event.name for event in events.events] == ["loop.started", "loop.finished"]


@pytest.mark.asyncio
async def test_capability_mutation_is_rejected_without_changing_state() -> None:
    composition, state, _action, _events = harness(
        [answer("unreachable")],
        registrations=(registration(MutatingContextCapability()),),
    )

    outcome = await run_loop(composition)

    assert outcome == FailLoop(
        reason="capability attempted to mutate canonical Loop state",
        error_code="RUNTIME_CONTRACT_VIOLATION",
    )
    assert state.state.extension_state == {}


@pytest.mark.asyncio
async def test_budget_exhaustion_is_a_typed_blocked_outcome() -> None:
    composition, state, _action, _events = harness(
        [tool()],
        max_turns=1,
        observations=[LoopObservation(kind="tool", status="succeeded")],
    )

    outcome = await run_loop(composition)

    assert outcome == BlockLoop(
        reason="Agent turn budget exhausted before a terminal outcome.",
        error_code="TURN_BUDGET_EXHAUSTED",
    )
    assert [saved.kind for _, saved in state.saves] == ["continue", "blocked"]


@pytest.mark.asyncio
async def test_action_failure_is_classified() -> None:
    composition, state, action, _events = harness([tool()])
    action.failure = RuntimeError("provider offline")

    outcome = await run_loop(composition)

    assert outcome == FailLoop(
        reason="provider offline",
        error_code="RUNTIME_FAILURE",
    )
    assert [saved.kind for _, saved in state.saves] == ["failed"]
