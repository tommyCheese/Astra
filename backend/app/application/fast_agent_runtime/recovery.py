from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.common.schemas.agent.fast_runtime import FastAgentAction, FastObservation
from app.common.schemas.agent.run_policy import FastRuntimeSnapshot
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


@dataclass(frozen=True)
class FastRecoveryResult:
    snapshot: FastRuntimeSnapshot
    observations: list[FastObservation]
    approved_tool_call: ToolCallRecord | None = None
    replay_action: FastAgentAction | None = None
    result_unknown: bool = False
    waiting_for_approval: bool = False


class FastRecovery:
    """Interpret only the durable state owned by fast-v1."""

    async def recover(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        snapshot: FastRuntimeSnapshot,
    ) -> FastRecoveryResult:
        observations = [
            FastObservation.model_validate(item) for item in snapshot.recent_observations
        ]
        pending = snapshot.pending_action
        if pending is None:
            approved = await repo.get_approved_tool_call(run_id)
            return FastRecoveryResult(snapshot, observations, approved_tool_call=approved)

        if pending.kind == "model":
            observations.append(
                FastObservation(
                    kind="model_error",
                    status="failed",
                    summary="The interrupted model call will be retried.",
                    data={"category": "interrupted_model_call", "retryable": True},
                )
            )
            return FastRecoveryResult(
                self._cleared(snapshot, observations), observations
            )

        call = await self._tool_call(repo, run_id, pending.action_id)
        if call is not None and call.status == "succeeded" and call.output is not None:
            observations.append(
                FastObservation(
                    kind="tool_result",
                    status="succeeded",
                    summary=f"Recovered recorded result from {call.tool_name}.",
                    tool_name=call.tool_name,
                    tool_call_id=call.id,
                    data=dict(call.output.get("data") or {}),
                    artifacts=list(call.output.get("artifacts") or []),
                )
            )
            return FastRecoveryResult(
                self._cleared(snapshot, observations), observations
            )
        if call is not None and call.status in {"failed", "rejected", "cancelled"}:
            observations.append(
                FastObservation(
                    kind="tool_error",
                    status="failed",
                    summary=f"Recovered failure from {call.tool_name}.",
                    tool_name=call.tool_name,
                    tool_call_id=call.id,
                    data={"category": (call.error or {}).get("category", call.status)},
                )
            )
            return FastRecoveryResult(
                self._cleared(snapshot, observations), observations
            )
        if call is not None and call.status == "approved":
            return FastRecoveryResult(snapshot, observations, approved_tool_call=call)
        if call is not None and call.status == "awaiting_approval":
            return FastRecoveryResult(snapshot, observations, waiting_for_approval=True)

        action = self._replay_action(pending)
        if pending.phase == "decided" or pending.idempotent is True:
            return FastRecoveryResult(
                self._cleared(snapshot, observations),
                observations,
                replay_action=action,
            )

        if call is not None:
            call.status = "result_unknown"
            call.completed_at = utc_now()
            call.error = {
                "category": "non_idempotent_result_unknown",
                "message": "The interrupted non-idempotent tool outcome is unknown.",
            }
        observations.append(
            FastObservation(
                kind="tool_error",
                status="failed",
                summary=(
                    "The previous non-idempotent tool may have run, so Fast Runtime "
                    "will not repeat it without user direction."
                ),
                tool_name=pending.tool_name,
                tool_call_id=call.id if call else None,
                data={"category": "non_idempotent_result_unknown", "retryable": False},
            )
        )
        return FastRecoveryResult(
            self._cleared(snapshot, observations),
            observations,
            result_unknown=True,
        )

    @staticmethod
    def _replay_action(pending) -> FastAgentAction | None:
        if not pending.tool_name:
            return None
        return FastAgentAction(
            action="call_tool",
            tool_name=pending.tool_name,
            tool_input=pending.tool_input,
            reason="Recover an interrupted Fast tool action.",
        )

    @staticmethod
    def _cleared(
        snapshot: FastRuntimeSnapshot,
        observations: list[FastObservation],
    ) -> FastRuntimeSnapshot:
        return snapshot.model_copy(
            update={
                "snapshot_version": snapshot.snapshot_version + 1,
                "pending_action": None,
                "recent_observations": [item.model_dump(mode="json") for item in observations[-100:]],
            }
        )

    @staticmethod
    async def _tool_call(
        repo: RunUnitOfWork,
        run_id: str,
        tool_call_id: str,
    ) -> ToolCallRecord | None:
        result = await repo.session.execute(
            select(ToolCallRecord)
            .where(ToolCallRecord.id == tool_call_id, ToolCallRecord.run_id == run_id)
            .options(selectinload(ToolCallRecord.approval_request))
        )
        return result.scalar_one_or_none()
