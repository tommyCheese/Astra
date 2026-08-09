"""Production composition for standard answer mode."""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, ClassVar, cast

from app.application.agent_runtime.composition import (
    CapabilityRegistration,
    RuntimePorts,
    build_standard_composition,
    never_cancelled,
)
from app.application.agent_runtime.contracts import (
    ActionProvider,
    CapabilityIdentity,
    CapabilitySlot,
    ContextContribution,
    LoopAction,
    LoopObservation,
    LoopOutcome,
    LoopState,
    ModelDecision,
    PendingAction,
    PortIdentity,
    SafetyInvariant,
    WaitLoop,
    port_identity,
)
from app.application.agent_runtime.loop import run_loop
from app.application.agent_runtime.services.tooling.action_boundary import ActionBoundary
from app.application.agent_runtime.services.tooling.plugin_runtime import (
    PluginRuntimeState,
)
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_policy import RunExecutionProfile
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.model_clients.contracts import (
    AnswerDeltaCallback,
    ModelClient,
    ModelOutputError,
)
from app.infrastructure.repositories.permissions import PermissionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.runtime.standard_checkpoint import (
    StandardRuntimeMetrics,
    StandardStatePort,
    _observation_payload,
)
from app.infrastructure.tools.base import AstraToolRegistry
from app.infrastructure.tools.router import ToolRouter

STANDARD_FORBIDDEN_SKILL_CAPABILITIES = frozenset(
    {
        "planning",
        "verification",
        "reflection",
        "subagent",
        "delegation_create",
        "memory_write",
    }
)

StandardTerminalCallback = Callable[
    [LoopOutcome, StandardRuntimeMetrics],
    Awaitable[None],
]


@dataclass
class StandardModelPort:
    model: ModelClient
    state_port: StandardStatePort
    on_answer_delta: AnswerDeltaCallback | None
    metrics: StandardRuntimeMetrics
    max_retries: int
    identity: ClassVar[PortIdentity] = port_identity("standard-model", "2")

    async def decide(self, state: LoopState, _context: tuple[ContextContribution, ...]) -> ModelDecision:
        resumed = self.state_port.take_resume_action()
        if resumed is not None:
            return ModelDecision(action=resumed, reasoning_summary=resumed.reason or "")
        model_context = _standard_context(state, _context)
        return await self._request_decision(state, model_context)

    async def _request_decision(self, state: LoopState, model_context: dict[str, object]) -> ModelDecision:
        for attempt in range(self.max_retries + 1):
            await self.state_port.set_pending(PendingAction(action_id=str(uuid.uuid4()), kind="model"))
            try:
                raw = await self.model.standard_decide(
                    state.goal,
                    cast(dict, model_context),
                    on_delta=self._observe_delta if self.on_answer_delta else None,
                )
                return await self._canonical_decision(raw, state.turn_index)
            except (ModelOutputError, ValueError) as exc:
                self.metrics.model_calls += 1
                await self.state_port.set_pending(None)
                if attempt >= self.max_retries:
                    return ModelDecision(
                        action=LoopAction(
                            kind="stop",
                            content="模型输出无法解析，快速模式已停止。",
                            reason=str(exc),
                        ),
                        reasoning_summary=str(exc),
                    )
        raise AssertionError("model retry loop did not return")

    async def _canonical_decision(self, raw: dict, turn_index: int) -> ModelDecision:
        self.metrics.model_calls += 1
        await self.state_port.set_pending(None)
        action = _canonical_model_action(raw)
        await self.state_port.record_answer_adoption(action.reason or "", turn_index)
        return ModelDecision(
            action=action,
            reasoning_summary=action.reason or "",
        )

    async def _observe_delta(self, delta: str) -> None:
        if delta and delta not in {"\0", "\1"} and self.metrics.first_token_latency_ms is None:
            self.metrics.first_token_latency_ms = self.metrics.elapsed_ms
        if self.on_answer_delta is not None:
            await self.on_answer_delta(delta)


@dataclass
class StandardActionPort:
    settings: AstraRuntimeSettings
    repository: RunUnitOfWork
    run_id: str
    state_port: StandardStatePort
    router: ToolRouter
    metrics: StandardRuntimeMetrics
    boundary: ActionBoundary = field(init=False)
    identity: ClassVar[PortIdentity] = ActionBoundary.identity

    def __post_init__(self) -> None:
        self.boundary = ActionBoundary(
            self.settings,
            self.repository,
            self.router,
            "fast",
        )

    async def execute(
        self,
        state: LoopState,
        action: LoopAction,
        _providers: tuple[ActionProvider, ...],
    ) -> LoopObservation:
        self.metrics.tool_actions += 1
        if state.pending_action is None:
            await self.state_port.set_pending(PendingAction(action_id=str(uuid.uuid4()), kind="tool", action=action))
        self.boundary.approved_tool_call = self.state_port.approved_tool_call
        self.boundary.on_prepared = lambda call, idempotent, waiting: self._prepared(action, call, idempotent, waiting)
        result = await self.boundary.execute(state, action, _providers)
        self.state_port.approved_tool_call = None
        if result.status != "waiting":
            await self.state_port.set_pending(None)
        return result

    async def _prepared(
        self,
        action: LoopAction,
        tool_call: ToolCallRecord,
        idempotent: bool,
        waiting: bool,
    ) -> None:
        await self.state_port.set_pending(
            PendingAction(
                action_id=tool_call.id,
                kind="approval" if waiting else "tool",
                phase="waiting" if waiting else "executing",
                action=action,
                idempotent=idempotent,
            )
        )


STANDARD_CANCELLATION_IDENTITY = port_identity("standard-cancellation", "4", SafetyInvariant.cancellation)


@dataclass(frozen=True)
class StandardProgressPolicy:
    identity: ClassVar[CapabilityIdentity] = CapabilityIdentity(
        name="standard-waiting",
        version=1,
        digest="6" * 64,
        slots=(CapabilitySlot.progress,),
        order=0,
    )

    async def evaluate(self, _state: LoopState, observation: LoopObservation) -> LoopOutcome | None:
        if observation.status == "waiting":
            return WaitLoop(reason=observation.summary)
        return None


@dataclass(frozen=True)
class StandardContextCapability:
    router: ToolRouter
    active_skills: tuple[dict[str, object], ...]
    identity: ClassVar[CapabilityIdentity] = CapabilityIdentity(
        name="standard-context",
        version=1,
        digest="7" * 64,
        slots=(CapabilitySlot.context,),
        order=0,
    )

    async def contribute(self, state: LoopState) -> ContextContribution:
        specs, unavailable = self.router.eligible_specs()
        manifests = {
            spec.name: {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.input_schema,
                "permission": spec.permission,
                "side_effect_level": spec.side_effect_level,
                "task_capabilities": spec.task_capabilities,
            }
            for _, spec in sorted(specs.items())
            if "delegation_create" not in spec.permissions
            and "memory_write" not in spec.permissions
            and "memory_delete" not in spec.permissions
        }
        return ContextContribution(
            source="standard-runtime",
            items=(
                {
                    "runtime": "fast-v1",
                    "answer_mode": "standard",
                    "messages": list(state.messages),
                    "recent_observations": [_observation_payload(item) for item in state.observations],
                    "tool_manifests": manifests,
                    "unavailable_tools": unavailable,
                    "active_skills": standard_compatible_skills(list(self.active_skills)),
                    "allowed_actions": ["answer", "call_tool", "ask_user", "stop"],
                },
            ),
        )


async def run_standard_runtime(
    *,
    settings: AstraRuntimeSettings,
    model_client: ModelClient,
    router: ToolRouter,
    repository: RunUnitOfWork,
    run_id: str,
    goal: str,
    active_skills: list[dict[str, object]] | None = None,
    on_answer_delta: AnswerDeltaCallback | None = None,
    on_terminal: StandardTerminalCallback | None = None,
    event_port: Any,
) -> LoopOutcome:
    metrics = StandardRuntimeMetrics()
    run = await repository.require_run_core(run_id)
    profile = RunExecutionProfile.model_validate(run.execution_profile or {})
    policy = profile.fast_runtime_policy
    if policy is None:
        raise ValueError("standard Run is missing its Runtime policy")
    state = StandardStatePort(
        repository,
        run,
        run_id,
        goal,
        policy.max_consecutive_tool_actions,
    )
    await _freeze_standard_catalog(repository, run_id, router.registry)
    ports = RuntimePorts(
        identities=(
            StandardModelPort.identity,
            state.identity,
            StandardActionPort.identity,
            STANDARD_CANCELLATION_IDENTITY,
            event_port.identity,
        ),
        model=StandardModelPort(model_client, state, on_answer_delta, metrics, policy.max_protocol_retries),
        load=state.load,
        recover=state.recover,
        save=state.save,
        action=StandardActionPort(settings, repository, run_id, state, router, metrics).execute,
        cancellation=never_cancelled,
        event=event_port.publish,
    )
    model_port = cast(StandardModelPort, ports.model)
    ports = RuntimePorts(**{**ports.__dict__, "model": model_port.decide})
    waiting = StandardProgressPolicy()
    context = StandardContextCapability(router, tuple(active_skills or []))
    composition = build_standard_composition(
        ports=ports,
        registrations=(
            CapabilityRegistration(context.identity, context.contribute),
            CapabilityRegistration(waiting.identity, waiting.evaluate),
        ),
    )
    outcome = await run_loop(composition)
    if on_terminal is not None:
        await on_terminal(outcome, metrics)
    return outcome


def standard_compatible_skills(
    skills: list[dict[str, object]],
) -> list[dict[str, object]]:
    compatible: list[dict[str, object]] = []
    for skill in skills:
        metadata_value = skill.get("metadata")
        metadata = metadata_value if isinstance(metadata_value, dict) else {}
        capabilities = set(metadata.get("required_capabilities") or [])
        if metadata.get("recommended_answer_mode") == "trusted":
            continue
        if metadata.get("runtime") in {"trusted", "trusted-v1"}:
            continue
        if metadata.get("trusted_only") is True:
            continue
        if capabilities & STANDARD_FORBIDDEN_SKILL_CAPABILITIES:
            continue
        compatible.append(skill)
    return compatible


def _standard_context(
    state: LoopState,
    contributions: tuple[ContextContribution, ...],
) -> dict[str, object]:
    merged: dict[str, object] = {}
    for contribution in contributions:
        for item in contribution.items:
            merged.update(item)
    observations = [_observation_payload(item) for item in state.observations]
    merged.setdefault("messages", list(state.messages))
    merged.setdefault("recent_observations", observations)
    merged["observations"] = observations
    return merged


async def _freeze_standard_catalog(
    repository: RunUnitOfWork,
    run_id: str,
    registry: AstraToolRegistry,
) -> None:
    plugins = PluginRuntimeState.from_registry(registry)
    catalog = [spec.model_dump(mode="json") for _, spec in sorted(registry.specs().items())]
    digest = sha256(json.dumps(catalog, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    await PermissionRepository(repository.session).freeze_tool_catalog(
        run_id,
        catalog=catalog,
        digest=digest,
        behavioral_catalog=plugins.snapshot_catalog(registry),
        behavioral_digest=plugins.behavioral_digest(registry),
        display_digest=plugins.display_digest(registry),
    )


def _canonical_model_action(value: dict[str, Any]) -> LoopAction:
    allowed = {
        "protocol_version",
        "action",
        "content",
        "tool_name",
        "tool_input",
        "reason",
    }
    unknown = set(value) - allowed
    if unknown:
        raise ValueError(f"unexpected standard action fields: {sorted(unknown)}")
    source_kind = value.get("action")
    kinds = {
        "call_tool": "tool",
        "answer": "answer",
        "ask_user": "ask_user",
        "stop": "stop",
    }
    if source_kind not in kinds:
        raise ValueError(f"unsupported standard action: {source_kind}")
    return LoopAction(
        kind=kinds[source_kind],
        name=value.get("tool_name"),
        input=value.get("tool_input") or {},
        content=value.get("content"),
        reason=value.get("reason"),
    )
