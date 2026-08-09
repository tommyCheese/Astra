"""Deterministic capability composition for the single Agent Runtime."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import partial
from hashlib import sha256
from typing import TypeAlias

from pydantic import Field

from app.application.agent_runtime.contracts import (
    ActionPort,
    ActionProvider,
    CancellationPort,
    CapabilityIdentity,
    CapabilitySlot,
    CompletionPolicy,
    ContextContributor,
    DecisionPolicy,
    EventPort,
    LifecycleObserver,
    ModelPort,
    ObservationProcessor,
    PortIdentity,
    ProgressPolicy,
    RuntimeValue,
    SafetyInvariant,
    StateLoader,
    StateRecovery,
    StateSaver,
)


async def never_cancelled(_run_id: str) -> bool:
    return False


async def ignore_runtime_event(_event: object) -> None:
    return None


STANDARD_PROFILE = "standard-v1"
TRUSTED_PROFILE = "trusted-v1"
PROFILE_ALIASES = {
    "fast-v1": STANDARD_PROFILE,
    STANDARD_PROFILE: STANDARD_PROFILE,
    TRUSTED_PROFILE: TRUSTED_PROFILE,
}
REQUIRED_SAFETY_COVERAGE = frozenset(SafetyInvariant)
RuntimeCapability: TypeAlias = (
    ContextContributor
    | DecisionPolicy
    | ActionProvider
    | ObservationProcessor
    | ProgressPolicy
    | CompletionPolicy
    | LifecycleObserver
)


class CompositionError(ValueError):
    """Raised when a Runtime composition is unsafe or non-deterministic."""


class CompositionIdentity(RuntimeValue):
    profile: str
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    ports: tuple[PortIdentity, ...]
    capabilities: tuple[CapabilityIdentity, ...]


@dataclass(frozen=True)
class RuntimePorts:
    identities: tuple[PortIdentity, PortIdentity, PortIdentity, PortIdentity, PortIdentity]
    model: ModelPort
    load: StateLoader
    recover: StateRecovery
    save: StateSaver
    action: ActionPort
    cancellation: CancellationPort
    event: EventPort


@dataclass(frozen=True)
class CapabilityRegistration:
    identity: CapabilityIdentity
    implementation: RuntimeCapability


class RuntimeComposition:
    """Validated immutable assembly of ports and ordered capability providers."""

    def __init__(
        self,
        *,
        profile: str,
        version: int,
        ports: RuntimePorts,
        registrations: Iterable[CapabilityRegistration],
    ) -> None:
        canonical_profile = PROFILE_ALIASES.get(profile)
        if canonical_profile is None:
            raise CompositionError(f"unknown Runtime profile: {profile}")
        if version < 1:
            raise CompositionError("Runtime composition version must be positive")
        self.profile = canonical_profile
        self.version = version
        self.ports = ports
        self.registrations = tuple(sorted(registrations, key=lambda item: (item.identity.order, item.identity.name)))
        self._validate()

    @property
    def identity(self) -> CompositionIdentity:
        ports = self.ports.identities
        capabilities = tuple(item.identity for item in self.registrations)
        payload = "|".join(
            (
                self.profile,
                str(self.version),
                *(identity.model_dump_json() for identity in ports),
                *(identity.model_dump_json() for identity in capabilities),
            )
        )
        return CompositionIdentity(
            profile=self.profile,
            version=self.version,
            digest=sha256(payload.encode()).hexdigest(),
            ports=ports,
            capabilities=capabilities,
        )

    def providers(self, slot: CapabilitySlot) -> tuple[RuntimeCapability, ...]:
        return tuple(item.implementation for item in self.registrations if slot in item.identity.slots)

    def ensure_resumable(self, persisted: CompositionIdentity) -> None:
        persisted_profile = PROFILE_ALIASES.get(persisted.profile)
        current = self.identity
        if persisted_profile != current.profile or persisted.version != current.version:
            raise CompositionError("persisted Runtime profile is not resumable")
        if persisted.digest != current.digest:
            raise CompositionError("persisted Runtime capability digest has drifted")

    def _validate(self) -> None:
        self._validate_ports()
        identities = [item.identity for item in self.registrations]
        self._validate_identities(identities)

    def _validate_ports(self) -> None:
        port_values = (
            self.ports.model,
            self.ports.load,
            self.ports.recover,
            self.ports.save,
            self.ports.action,
            self.ports.cancellation,
            self.ports.event,
        )
        if any(port is None for port in port_values):
            raise CompositionError("all mandatory Runtime ports must be provided")
        identities = list(self.ports.identities)
        if any(not identity.trusted for identity in identities):
            raise CompositionError("Runtime ports must be platform-trusted")
        names = [identity.name for identity in identities]
        if len(names) != len(set(names)):
            raise CompositionError("mandatory Runtime port owners must be unique")
        self._validate_safety_coverage(identities)

    @staticmethod
    def _validate_safety_coverage(identities: list[PortIdentity]) -> None:
        coverage = frozenset(invariant for identity in identities for invariant in identity.safety_coverage)
        missing = REQUIRED_SAFETY_COVERAGE - coverage
        if missing:
            names = ", ".join(sorted(invariant.value for invariant in missing))
            raise CompositionError(f"missing mandatory safety coverage: {names}")

    @staticmethod
    def _validate_identities(identities: list[CapabilityIdentity]) -> None:
        identity_keys = [(item.name, item.version) for item in identities]
        if len(identity_keys) != len(set(identity_keys)):
            raise CompositionError("capability identity and version must be unique")
        if any(not item.trusted for item in identities):
            raise CompositionError("Runtime capabilities must be platform-trusted")
        orders = [(slot, item.order) for item in identities for slot in item.slots]
        if len(orders) != len(set(orders)):
            raise CompositionError("capability order must be unique within each slot")


build_standard_composition = partial(RuntimeComposition, profile=STANDARD_PROFILE, version=1)
build_trusted_composition = partial(RuntimeComposition, profile=TRUSTED_PROFILE, version=1)
