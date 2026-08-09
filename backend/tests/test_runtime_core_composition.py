from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import TypeAdapter, ValidationError

from app.application.agent_runtime.action import ActionBoundary
from app.application.agent_runtime.composition import (
    CapabilityRegistration,
    CompositionError,
    RuntimeComposition,
    RuntimePorts,
    build_standard_composition,
    build_trusted_composition,
)
from app.application.agent_runtime.contracts import (
    CapabilityIdentity,
    CapabilitySlot,
    CompleteLoop,
    LoopAction,
    LoopObservation,
    LoopOutcome,
    LoopState,
    PortIdentity,
    SafetyInvariant,
)


@dataclass(frozen=True)
class FakeCapability:
    identity: CapabilityIdentity


def identity(
    name: str,
    slots: CapabilitySlot | tuple[CapabilitySlot, ...],
    order: int,
    *,
    digest_character: str = "a",
    trusted: bool = True,
) -> CapabilityIdentity:
    declared_slots = (slots,) if isinstance(slots, CapabilitySlot) else slots
    return CapabilityIdentity(
        name=name,
        version=1,
        digest=digest_character * 64,
        slots=declared_slots,
        order=order,
        trusted=trusted,
    )


def registration(capability_identity: CapabilityIdentity) -> CapabilityRegistration:
    async def unused(*_args):
        return None

    return CapabilityRegistration(capability_identity, unused)


def representative_registrations() -> tuple[CapabilityRegistration, ...]:
    return tuple(
        registration(identity(slot.value, slot, index))
        for index, slot in enumerate(
            (
                CapabilitySlot.decision,
                CapabilitySlot.action,
                CapabilitySlot.observation,
                CapabilitySlot.progress,
                CapabilitySlot.completion,
            )
        )
    )


def runtime_ports(**replacements) -> RuntimePorts:
    def port(
        name: str,
        digest_character: str,
        *coverage: SafetyInvariant,
    ) -> PortIdentity:
        return PortIdentity(
            name=name,
            version=1,
            digest=digest_character * 64,
            safety_coverage=frozenset(coverage),
        )

    async def unused(*_args):
        return None

    identities = (
        port("model", "0"),
        port(
            "state",
            "1",
            SafetyInvariant.persistence,
            SafetyInvariant.result_unknown_recovery,
        ),
        port(
            "action",
            "2",
            SafetyInvariant.schema_validation,
            SafetyInvariant.effect_analysis,
            SafetyInvariant.authorization,
            SafetyInvariant.approval_integrity,
        ),
        port("cancellation", "3", SafetyInvariant.cancellation),
        port("event", "4"),
    )
    values = {
        "identities": identities,
        "model": unused,
        "load": unused,
        "recover": unused,
        "save": unused,
        "action": unused,
        "cancellation": unused,
        "event": unused,
    }
    values.update(replacements)
    return RuntimePorts(**values)


def test_canonical_state_and_discriminated_outcome_round_trip() -> None:
    state = LoopState(
        run_id="run-1",
        task_id="task-1",
        goal="ship a clean Runtime",
        max_turns=8,
        observations=(LoopObservation(kind="tool", status="succeeded", data={"value": 3}),),
    )
    restored_state = LoopState.model_validate_json(state.model_dump_json())
    outcome_adapter = TypeAdapter(LoopOutcome)
    outcome = CompleteLoop(answer="done", data={"verified": True})
    restored_outcome = outcome_adapter.validate_json(outcome.model_dump_json())

    assert restored_state == state
    assert restored_outcome == outcome
    assert restored_outcome.kind == "completed"


def test_action_contract_rejects_invalid_variant_fields() -> None:
    with pytest.raises(ValidationError, match="tool actions require a name"):
        LoopAction(kind="tool")
    with pytest.raises(ValidationError, match="only tool actions"):
        LoopAction(kind="stop", name="workspace_read")


def test_capability_slots_are_fixed_and_explicit() -> None:
    assert {slot.value for slot in CapabilitySlot} == {
        "context",
        "decision",
        "action",
        "observation",
        "progress",
        "completion",
        "lifecycle",
    }


def test_standard_and_trusted_builders_freeze_profile_identity() -> None:
    standard = build_standard_composition(ports=runtime_ports(), registrations=representative_registrations())
    trusted = build_trusted_composition(ports=runtime_ports(), registrations=representative_registrations())

    assert standard.identity.profile == "standard-v1"
    assert trusted.identity.profile == "trusted-v1"
    assert standard.identity.capabilities == trusted.identity.capabilities


def test_profiles_share_one_mandatory_action_boundary_identity() -> None:
    assert ActionBoundary.identity.name == "shared-action-boundary"
    assert SafetyInvariant.authorization in ActionBoundary.identity.safety_coverage


def test_composition_rejects_missing_mandatory_safety_stage() -> None:
    ports = runtime_ports()
    identities = list(ports.identities)
    identities[2] = identities[2].model_copy(update={"safety_coverage": frozenset()})
    with pytest.raises(CompositionError, match="approval_integrity"):
        build_standard_composition(
            ports=runtime_ports(identities=tuple(identities)),
            registrations=representative_registrations(),
        )


def test_composition_rejects_missing_mandatory_port() -> None:
    missing_event = runtime_ports(event=None)
    with pytest.raises(CompositionError, match="mandatory Runtime ports"):
        build_standard_composition(ports=missing_event, registrations=representative_registrations())


def test_composition_rejects_duplicate_identity() -> None:
    registrations = representative_registrations() + (registration(identity("decision", CapabilitySlot.context, 0)),)
    with pytest.raises(CompositionError, match="identity and version"):
        build_standard_composition(ports=runtime_ports(), registrations=registrations)


def test_composition_rejects_ordering_conflict() -> None:
    registrations = representative_registrations() + (
        registration(identity("context-a", CapabilitySlot.context, 0)),
        registration(identity("context-b", CapabilitySlot.context, 0)),
    )
    with pytest.raises(CompositionError, match="order must be unique"):
        build_standard_composition(ports=runtime_ports(), registrations=registrations)


def test_composition_rejects_duplicate_mandatory_port_owner() -> None:
    ports = runtime_ports()
    identities = (*ports.identities[:4], ports.identities[0])
    duplicate_owner = runtime_ports(identities=identities)
    with pytest.raises(CompositionError, match="port owners must be unique"):
        build_standard_composition(ports=duplicate_owner, registrations=representative_registrations())


def test_composition_rejects_untrusted_registration() -> None:
    registrations = representative_registrations() + (
        registration(identity("external-context", CapabilitySlot.context, 0, trusted=False)),
    )
    with pytest.raises(CompositionError, match="platform-trusted"):
        build_standard_composition(ports=runtime_ports(), registrations=registrations)


def test_resume_accepts_persisted_runtime_alias_and_rejects_digest_drift() -> None:
    composition = build_standard_composition(ports=runtime_ports(), registrations=representative_registrations())
    persisted_fast = composition.identity.model_copy(update={"profile": "fast-v1"})

    composition.ensure_resumable(persisted_fast)

    drifted = composition.identity.model_copy(update={"digest": "f" * 64})
    with pytest.raises(CompositionError, match="digest has drifted"):
        composition.ensure_resumable(drifted)


def test_composition_order_and_digest_are_deterministic() -> None:
    registrations = representative_registrations()
    forward = RuntimeComposition(
        profile="standard-v1",
        version=1,
        ports=runtime_ports(),
        registrations=registrations,
    )
    reverse = RuntimeComposition(
        profile="fast-v1",
        version=1,
        ports=runtime_ports(),
        registrations=reversed(registrations),
    )

    assert forward.identity == reverse.identity


def test_one_capability_can_implement_multiple_typed_slots() -> None:
    shared = registration(
        identity(
            "trusted-verification",
            (CapabilitySlot.observation, CapabilitySlot.completion),
            20,
        )
    )
    composition = build_trusted_composition(
        ports=runtime_ports(),
        registrations=representative_registrations() + (shared,),
    )

    assert shared.implementation in composition.providers(CapabilitySlot.observation)
    assert shared.implementation in composition.providers(CapabilitySlot.completion)
