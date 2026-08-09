"""Recovery decisions for canonical standard Runtime checkpoints."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.application.agent_runtime.contracts import (
    LoopAction,
    LoopObservation,
    LoopOutcome,
    LoopState,
    PendingAction,
    WaitLoop,
)
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

CheckpointWriter = Callable[[LoopState], Awaitable[None]]
RecoveryResult = tuple[
    LoopState,
    LoopOutcome | None,
    LoopAction | None,
    ToolCallRecord | None,
]


async def recover_standard_checkpoint(
    repository: RunUnitOfWork,
    state: LoopState,
    persist: CheckpointWriter,
) -> RecoveryResult:
    pending = state.pending_action
    if pending is None:
        approved = await repository.get_approved_tool_call(state.run_id)
        return state, None, None, approved
    if pending.kind == "model":
        observation = LoopObservation(
            kind="model_error",
            status="failed",
            summary="The interrupted model call will be retried.",
            data={"category": "interrupted_model_call", "retryable": True},
        )
        return await _cleared(state, persist, observation), None, None, None
    call = await _tool_call(repository, state.run_id, pending.action_id)
    return await _recover_action(repository, state, pending, call, persist)


async def _recover_action(
    repository: RunUnitOfWork,
    state: LoopState,
    pending: PendingAction,
    call: ToolCallRecord | None,
    persist: CheckpointWriter,
) -> RecoveryResult:
    if call is not None and call.status == "succeeded" and call.output is not None:
        updated = await _recorded_result(state, call, persist)
        return updated, None, None, None
    if call is not None and call.status in {"failed", "rejected", "cancelled"}:
        updated = await _recorded_failure(state, call, persist)
        return updated, None, None, None
    if call is not None and call.status == "approved":
        return state, None, None, call
    if call is not None and call.status == "awaiting_approval":
        return state, WaitLoop(reason="等待批准先前的工具调用。"), None, None
    if pending.phase == "decided" or pending.idempotent is True:
        updated = await _cleared(state, persist)
        return updated, None, pending.action, None
    updated, waiting = await _result_unknown(state, pending, call, persist)
    return updated, waiting, None, None


async def _recorded_result(
    state: LoopState,
    call: ToolCallRecord,
    persist: CheckpointWriter,
) -> LoopState:
    output = call.output or {}
    return await _cleared(
        state,
        persist,
        LoopObservation(
            kind="tool_result",
            status="succeeded",
            summary=f"Recovered recorded result from {call.tool_name}.",
            data={
                **dict(output.get("data") or {}),
                "tool_name": call.tool_name,
                "tool_call_id": call.id,
                "artifacts": list(output.get("artifacts") or []),
            },
        ),
    )


async def _recorded_failure(
    state: LoopState,
    call: ToolCallRecord,
    persist: CheckpointWriter,
) -> LoopState:
    return await _cleared(
        state,
        persist,
        LoopObservation(
            kind="tool_error",
            status="failed",
            summary=f"Recovered failure from {call.tool_name}.",
            data={
                "category": (call.error or {}).get("category", call.status),
                "tool_name": call.tool_name,
                "tool_call_id": call.id,
            },
        ),
    )


async def _result_unknown(
    state: LoopState,
    pending: PendingAction,
    call: ToolCallRecord | None,
    persist: CheckpointWriter,
) -> tuple[LoopState, WaitLoop]:
    if call is not None:
        call.status = "result_unknown"
        call.completed_at = utc_now()
        call.error = {
            "category": "non_idempotent_result_unknown",
            "message": "The interrupted non-idempotent tool outcome is unknown.",
        }
    observation = LoopObservation(
        kind="tool_error",
        status="unknown",
        summary=("The previous non-idempotent tool may have run, so Runtime will not repeat it without user direction."),
        data={
            "category": "non_idempotent_result_unknown",
            "retryable": False,
            "tool_name": pending.action.name if pending.action else None,
            "tool_call_id": call.id if call else None,
        },
    )
    updated = await _cleared(state, persist, observation)
    return updated, WaitLoop(reason=observation.summary)


async def _cleared(
    state: LoopState,
    persist: CheckpointWriter,
    observation: LoopObservation | None = None,
) -> LoopState:
    observations = state.observations
    if observation is not None:
        observations = (*observations, observation)
    updated = state.model_copy(
        update={
            "checkpoint_version": state.checkpoint_version + 1,
            "pending_action": None,
            "observations": observations,
        }
    )
    await persist(updated)
    return updated


async def _tool_call(
    repository: RunUnitOfWork,
    run_id: str,
    tool_call_id: str,
) -> ToolCallRecord | None:
    result = await repository.session.execute(
        select(ToolCallRecord)
        .where(ToolCallRecord.id == tool_call_id, ToolCallRecord.run_id == run_id)
        .options(selectinload(ToolCallRecord.approval_request))
    )
    return result.scalar_one_or_none()
