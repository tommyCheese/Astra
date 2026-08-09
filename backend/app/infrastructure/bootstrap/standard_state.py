"""Canonical checkpoint, recovery, and terminal persistence for standard Runtime."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from app.application.agent_runtime.contracts import (
    LoopAction,
    LoopObservation,
    LoopOutcome,
    LoopState,
    PendingAction,
    PortIdentity,
    SafetyInvariant,
)
from app.infrastructure.bootstrap.standard_recovery import recover_standard_checkpoint
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


def _port_identity(name: str, digest_character: str, *coverage: SafetyInvariant) -> PortIdentity:
    return PortIdentity(
        name=name,
        version=1,
        digest=digest_character * 64,
        safety_coverage=frozenset(coverage),
    )


@dataclass
class StandardRuntimeMetrics:
    started_at: float = field(default_factory=time.monotonic)
    model_calls: int = 0
    tool_actions: int = 0
    first_token_latency_ms: int | None = None

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started_at) * 1000)


@dataclass
class StandardStatePort:
    identity = _port_identity(
        "standard-state",
        "1",
        SafetyInvariant.persistence,
        SafetyInvariant.result_unknown_recovery,
    )

    _repo: RunUnitOfWork
    _run: RunRecord
    _run_id: str
    _goal: str
    _max_consecutive_tool_actions: int
    _state_cache: LoopState | None = None
    _resume_action: LoopAction | None = None
    approved_tool_call: ToolCallRecord | None = None

    async def load(self) -> LoopState:
        state = _read_checkpoint(
            self._run,
            self._goal,
            self._max_consecutive_tool_actions,
        )
        self._state_cache = state
        return state

    async def recover(self, state: LoopState) -> tuple[LoopState, LoopOutcome | None]:
        updated, outcome, resume_action, approved = await recover_standard_checkpoint(
            self._repo,
            state,
            self._persist_checkpoint,
        )
        self._resume_action = resume_action
        self.approved_tool_call = approved
        return updated, outcome

    async def save(self, state: LoopState, outcome: LoopOutcome) -> None:
        cached = self._require_cache()
        updated = state.model_copy(
            update={
                "checkpoint_version": cached.checkpoint_version + 1,
                "pending_action": cached.pending_action,
                "terminal_intent": _terminal_intent(outcome),
            }
        )
        await self._persist_checkpoint(updated)

    async def set_pending(self, pending: PendingAction | None) -> None:
        cached = self._require_cache()
        updated = cached.model_copy(
            update={
                "checkpoint_version": cached.checkpoint_version + 1,
                "pending_action": pending,
            }
        )
        await self._persist_checkpoint(updated)

    def take_resume_action(self) -> LoopAction | None:
        action = self._resume_action
        self._resume_action = None
        return action

    async def record_answer_adoption(self, reason: str, turn_index: int) -> None:
        event = _answer_adoption_event(reason, turn_index)
        if event is None:
            return
        event_type, payload = event
        await self._repo.add_event(self._run_id, event_type, payload)
        await self._repo.session.commit()

    async def _persist_checkpoint(self, state: LoopState) -> None:
        cached = self._require_cache()
        await self._repo.update_runtime_checkpoint(
            self._run_id,
            expected_version=cached.checkpoint_version,
            checkpoint=_checkpoint_payload(state),
        )
        await self._repo.session.commit()
        self._state_cache = state

    def _require_cache(self) -> LoopState:
        if self._state_cache is None:
            raise RuntimeError("standard checkpoint has not been loaded")
        return self._state_cache


def _read_checkpoint(
    run: RunRecord,
    goal: str,
    max_consecutive_tool_actions: int,
) -> LoopState:
    value = run.fast_runtime_snapshot or {}
    version = int(value.get("snapshot_version", 0))
    turn_index = int(value.get("turn_index", 0))
    observations = tuple(_read_observation(item) for item in value.get("recent_observations", []) if isinstance(item, dict))
    return LoopState(
        run_id=run.id,
        task_id=run.task_id,
        goal=goal,
        turn_index=turn_index,
        max_turns=turn_index + max_consecutive_tool_actions + 1,
        checkpoint_version=version,
        messages=tuple(value.get("messages", [])),
        observations=observations,
        pending_action=_read_pending(value.get("pending_action")),
        terminal_intent=value.get("terminal_intent"),
    )


def _read_pending(value: object) -> PendingAction | None:
    if not isinstance(value, dict):
        return None
    action_value = value.get("action")
    if isinstance(action_value, dict):
        action = LoopAction.model_validate(action_value)
    elif value.get("tool_name"):
        action = LoopAction(
            kind="tool",
            name=str(value["tool_name"]),
            input=cast(dict[str, Any], value.get("tool_input") or {}),
        )
    else:
        action = None
    return PendingAction(
        action_id=str(value["action_id"]),
        kind=value["kind"],
        phase=value.get("phase", "decided"),
        action=action,
        idempotent=value.get("idempotent"),
    )


def _read_observation(value: dict[str, Any]) -> LoopObservation:
    data = dict(value.get("data") or {})
    for key in ("tool_name", "tool_call_id", "artifacts"):
        if value.get(key) is not None:
            data[key] = value[key]
    status = value.get("status", "failed")
    return LoopObservation(
        kind=value.get("kind", "system"),
        status={"denied": "rejected"}.get(status, status),
        summary=value.get("summary", ""),
        data=data,
    )


def _checkpoint_payload(state: LoopState) -> dict[str, Any]:
    return {
        "protocol_version": 1,
        "snapshot_version": state.checkpoint_version,
        "turn_index": state.turn_index,
        "messages": list(state.messages),
        "recent_observations": [_observation_payload(item) for item in state.observations[-100:]],
        "pending_action": _pending_payload(state.pending_action),
        "terminal_intent": state.terminal_intent,
    }


def _pending_payload(pending: PendingAction | None) -> dict[str, Any] | None:
    if pending is None:
        return None
    action = pending.action
    return {
        "action_id": pending.action_id,
        "kind": pending.kind,
        "phase": pending.phase,
        "tool_name": action.name if action else None,
        "tool_input": action.input if action else {},
        "idempotent": pending.idempotent,
    }


def _observation_payload(observation: LoopObservation) -> dict[str, Any]:
    data = dict(observation.data)
    return {
        "kind": observation.kind,
        "status": {
            "rejected": "denied",
            "waiting": "denied",
            "unknown": "failed",
        }.get(observation.status, observation.status),
        "summary": observation.summary,
        "tool_name": data.pop("tool_name", None),
        "tool_call_id": data.pop("tool_call_id", None),
        "artifacts": data.pop("artifacts", []),
        "data": data,
    }


def _answer_adoption_event(reason: str, turn_index: int) -> tuple[str, dict[str, object]] | None:
    if reason.startswith("Adopted the already-streamed answer"):
        return "answer.schema_degraded", {
            "turn_index": turn_index,
            "answer_mode": "standard",
            "reason": reason,
        }
    if reason.startswith("Adopted the streamed answer"):
        return "answer.structure_adopted", {
            "turn_index": turn_index,
            "answer_mode": "standard",
        }
    return None


def _terminal_intent(
    outcome: LoopOutcome,
) -> Literal["answer", "ask_user", "stop"] | None:
    if outcome.kind == "completed":
        return "answer"
    if outcome.kind == "waiting":
        return "ask_user"
    if outcome.kind in {"blocked", "failed", "cancelled"}:
        return "stop"
    return None
