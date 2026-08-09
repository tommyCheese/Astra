"""Canonical values and ports for the single Agent Runtime loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from enum import StrEnum
from functools import partial
from typing import Any, Literal, TypeAlias, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

JsonObject: TypeAlias = dict[str, JsonValue]


class RuntimeValue(BaseModel):
    """Strict, immutable base for values crossing Runtime boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilitySlot(StrEnum):
    context = "context"
    decision = "decision"
    action = "action"
    observation = "observation"
    progress = "progress"
    completion = "completion"
    lifecycle = "lifecycle"


class CapabilityIdentity(RuntimeValue):
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    slots: tuple[CapabilitySlot, ...] = Field(min_length=1)
    order: int = Field(ge=0)
    trusted: bool = True


class SafetyInvariant(StrEnum):
    schema_validation = "schema_validation"
    effect_analysis = "effect_analysis"
    authorization = "authorization"
    approval_integrity = "approval_integrity"
    persistence = "persistence"
    cancellation = "cancellation"
    result_unknown_recovery = "result_unknown_recovery"


class PortIdentity(RuntimeValue):
    name: str = Field(min_length=1, max_length=120)
    version: int = Field(ge=1)
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    safety_coverage: frozenset[SafetyInvariant] = frozenset()
    trusted: bool = True


def port_identity(name: str, digest_character: str, *coverage: SafetyInvariant) -> PortIdentity:
    return PortIdentity(
        name=name,
        version=1,
        digest=digest_character * 64,
        safety_coverage=frozenset(coverage),
    )


class LoopAction(RuntimeValue):
    kind: Literal["tool", "answer", "ask_user", "stop"]
    name: str | None = None
    input: JsonObject = Field(default_factory=dict)
    content: str | None = None
    reason: str | None = None
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def _validate_shape(self) -> LoopAction:
        if self.kind == "tool" and not self.name:
            raise ValueError("tool actions require a name")
        if self.kind in {"answer", "ask_user"} and not self.content:
            raise ValueError(f"{self.kind} actions require content")
        if self.kind != "tool" and (self.name or self.input or self.idempotency_key):
            raise ValueError("only tool actions may carry invocation fields")
        return self


class PendingAction(RuntimeValue):
    action_id: str = Field(min_length=1, max_length=120)
    kind: Literal["model", "tool", "approval"]
    phase: Literal["decided", "prepared", "executing", "waiting"] = "decided"
    action: LoopAction | None = None
    idempotent: bool | None = None


class ModelDecision(RuntimeValue):
    action: LoopAction
    reasoning_summary: str = ""


class LoopObservation(RuntimeValue):
    kind: str = Field(min_length=1, max_length=120)
    status: Literal["succeeded", "waiting", "rejected", "failed", "unknown"]
    summary: str = ""
    data: JsonObject = Field(default_factory=dict)


def canonical_observation(
    value: Mapping[str, Any],
    *,
    status_aliases: Mapping[str, str] | None = None,
) -> LoopObservation:
    status = str(value.get("status", "failed"))
    normalized = (status_aliases or {}).get(status, status)
    if normalized not in {"succeeded", "waiting", "rejected", "failed", "unknown"}:
        normalized = "failed"
    return LoopObservation(
        kind=str(value.get("kind", "system")),
        status=cast(Any, normalized),
        summary=str(value.get("summary", "")),
        data=cast(JsonObject, value.get("data") or {}),
    )


class LoopState(RuntimeValue):
    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    turn_index: int = Field(default=0, ge=0)
    max_turns: int = Field(ge=1)
    checkpoint_version: int = Field(default=0, ge=0)
    messages: tuple[JsonObject, ...] = ()
    observations: tuple[LoopObservation, ...] = ()
    pending_action: PendingAction | None = None
    terminal_intent: Literal["answer", "ask_user", "stop"] | None = None
    extension_state: JsonObject = Field(default_factory=dict)


class LoopOutcome(RuntimeValue):
    kind: Literal["continue", "waiting", "completed", "blocked", "failed", "cancelled"]
    reason: str = ""
    answer: str = ""
    error_code: str = ""
    retryable: bool = False
    data: JsonObject = Field(default_factory=dict)
    state: JsonObject = Field(default_factory=dict)


def consume_outcome(outcome: LoopOutcome | None) -> tuple[None, LoopOutcome | None]:
    return None, outcome


ContinueLoop = partial(LoopOutcome, kind="continue")
WaitLoop = partial(LoopOutcome, kind="waiting")
CompleteLoop = partial(LoopOutcome, kind="completed")
BlockLoop = partial(LoopOutcome, kind="blocked")
FailLoop = partial(LoopOutcome, kind="failed")
CancelLoop = partial(LoopOutcome, kind="cancelled", reason="cancelled")


class ContextContribution(RuntimeValue):
    source: str = Field(min_length=1)
    items: tuple[JsonObject, ...] = ()


class RuntimeEvent(RuntimeValue):
    name: Literal[
        "loop.started",
        "turn.started",
        "decision.selected",
        "observation.recorded",
        "loop.finished",
    ]
    payload: JsonObject = Field(default_factory=dict)


ContextContributor: TypeAlias = Callable[[LoopState], Awaitable[ContextContribution]]
DecisionPolicy: TypeAlias = Callable[
    [LoopState, tuple[ContextContribution, ...], ModelDecision],
    Awaitable[ModelDecision],
]
ActionProvider: TypeAlias = Callable[[LoopState, LoopAction], Awaitable[LoopObservation]]
ObservationProcessor: TypeAlias = Callable[[LoopState, LoopObservation], Awaitable[LoopObservation]]
ProgressPolicy: TypeAlias = Callable[[LoopState, LoopObservation], Awaitable[LoopOutcome | None]]
CompletionPolicy: TypeAlias = Callable[
    [LoopState, ModelDecision, LoopObservation | None],
    Awaitable[LoopOutcome | None],
]
LifecycleObserver: TypeAlias = Callable[[LoopState, RuntimeEvent, LoopOutcome | None], Awaitable[None]]
ModelPort: TypeAlias = Callable[[LoopState, tuple[ContextContribution, ...]], Awaitable[ModelDecision]]
StateLoader: TypeAlias = Callable[[], Awaitable[LoopState]]
StateRecovery: TypeAlias = Callable[[LoopState], Awaitable[tuple[LoopState, LoopOutcome | None]]]
StateSaver: TypeAlias = Callable[[LoopState, LoopOutcome], Awaitable[None]]
ActionPort: TypeAlias = Callable[[LoopState, LoopAction, tuple[ActionProvider, ...]], Awaitable[LoopObservation]]
CancellationPort: TypeAlias = Callable[[str], Awaitable[bool]]
EventPort: TypeAlias = Callable[[RuntimeEvent], Awaitable[None]]
