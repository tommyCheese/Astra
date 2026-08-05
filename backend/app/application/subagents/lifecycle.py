from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.executions import (
    AgentBudgetReservationRecord,
    AgentExecutionRecord,
)
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import RunEventRecord
from app.infrastructure.db.models.workspaces import SandboxJobRecord
from app.infrastructure.repositories.agent_executions import (
    TERMINAL_AGENT_STATUSES,
    AgentExecutionRepository,
)


@dataclass(frozen=True)
class CancellationReport:
    cancelled_execution_ids: tuple[str, ...]
    cancelled_tool_call_ids: tuple[str, ...]
    result_unknown_tool_call_ids: tuple[str, ...]
    terminated_sandbox_job_ids: tuple[str, ...]
    immutable_effects: tuple[dict[str, Any], ...]


class SubagentCancellationService:
    """Durably fences an execution tree before cooperative task cancellation."""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.executions = AgentExecutionRepository(session)

    async def cancel_tree(
        self,
        execution_id: str,
        *,
        reason: str = "parent_cancelled",
        commit: bool = True,
    ) -> CancellationReport:
        root = await self.executions.require(execution_id)
        descendants = await self.executions.descendants(execution_id)
        targets = [root, *descendants]
        target_ids = [item.id for item in targets]
        now = utc_now()
        tool_calls = await self._tool_calls(target_ids)
        immutable_effects = _immutable_effects(tool_calls)
        active_calls = _active_tool_calls(tool_calls)
        unknown_ids = [
            call.id
            for call in active_calls
            if call.status == "running" and not _is_read_only(call.side_effect_level)
        ]
        cancelled_call_ids = [call.id for call in active_calls if call.id not in unknown_ids]
        await self._cancel_tool_calls(cancelled_call_ids, unknown_ids, reason, now)
        jobs = await self._cancel_sandbox_jobs(tool_calls, reason, now)
        cancelled_execution_ids = await self._cancel_executions(
            targets, reason, immutable_effects, unknown_ids, now
        )

        await self.session.execute(
            update(AgentBudgetReservationRecord)
            .where(
                AgentBudgetReservationRecord.child_execution_id.in_(target_ids),
                AgentBudgetReservationRecord.status == "reserved",
            )
            .values(status="cancelled", settled_at=now)
        )
        self.session.add(
            RunEventRecord(
                run_id=root.run_id,
                agent_execution_id=root.id,
                type="subagent.cancelled",
                payload={
                    "agent_execution_id": root.id,
                    "status": "cancelled",
                    "reason": reason,
                    "descendant_count": max(0, len(cancelled_execution_ids) - 1),
                    "immutable_effect_count": len(immutable_effects),
                    "result_unknown_count": len(unknown_ids),
                },
            )
        )
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return CancellationReport(
            cancelled_execution_ids=tuple(cancelled_execution_ids),
            cancelled_tool_call_ids=tuple(cancelled_call_ids),
            result_unknown_tool_call_ids=tuple(unknown_ids),
            terminated_sandbox_job_ids=tuple(job.id for job in jobs),
            immutable_effects=immutable_effects,
        )

    async def _tool_calls(self, target_ids):
        return list(
            (
                await self.session.scalars(
                    select(ToolCallRecord).where(ToolCallRecord.agent_execution_id.in_(target_ids))
                )
            ).all()
        )

    async def _cancel_tool_calls(self, cancelled_ids, unknown_ids, reason, now) -> None:
        if cancelled_ids:
            await self.session.execute(
                update(ToolCallRecord)
                .where(ToolCallRecord.id.in_(cancelled_ids))
                .values(
                    status="cancelled",
                    completed_at=now,
                    error={"category": "cancelled", "reason": reason},
                )
            )
        if unknown_ids:
            await self.session.execute(
                update(ToolCallRecord)
                .where(ToolCallRecord.id.in_(unknown_ids))
                .values(
                    status="result_unknown",
                    completed_at=now,
                    error={"category": "non_idempotent_result_unknown", "reason": reason},
                )
            )

    async def _cancel_sandbox_jobs(self, tool_calls, reason, now):
        tool_ids = [call.id for call in tool_calls]
        if not tool_ids:
            return []
        jobs = list(
            (
                await self.session.scalars(
                    select(SandboxJobRecord).where(
                        SandboxJobRecord.tool_call_id.in_(tool_ids),
                        SandboxJobRecord.status.in_(
                            ["queued", "preparing", "running", "collecting"]
                        ),
                    )
                )
            ).all()
        )
        if jobs:
            await self.session.execute(
                update(SandboxJobRecord)
                .where(SandboxJobRecord.id.in_([job.id for job in jobs]))
                .values(
                    status="cancelled",
                    completed_at=now,
                    exit_reason=reason,
                    error={"category": "cancelled", "reason": reason},
                )
            )
        return jobs

    async def _cancel_executions(self, targets, reason, immutable_effects, unknown_ids, now):
        cancelled_ids = []
        for execution in reversed(targets):
            if execution.status in TERMINAL_AGENT_STATUSES:
                continue
            outcome = await self.session.execute(
                update(AgentExecutionRecord)
                .where(
                    AgentExecutionRecord.id == execution.id,
                    AgentExecutionRecord.state_version == execution.state_version,
                    AgentExecutionRecord.cancellation_epoch == execution.cancellation_epoch,
                    AgentExecutionRecord.status.not_in(TERMINAL_AGENT_STATUSES),
                )
                .values(
                    status="cancelled",
                    phase="terminal",
                    wait_reason=reason,
                    error={
                        "category": "cancelled",
                        "reason": reason,
                        "immutable_effects": list(immutable_effects),
                        "result_unknown_tool_call_ids": unknown_ids,
                    },
                    worker_id=None,
                    fencing_token=AgentExecutionRecord.fencing_token + 1,
                    cancellation_epoch=AgentExecutionRecord.cancellation_epoch + 1,
                    state_version=AgentExecutionRecord.state_version + 1,
                    heartbeat_at=now,
                    finished_at=now,
                    updated_at=now,
                )
            )
            if outcome.rowcount == 1:
                cancelled_ids.append(execution.id)
        return cancelled_ids


def _is_read_only(side_effect_level: str) -> bool:
    return side_effect_level in {"none", "read", "read_only"}


def _active_tool_calls(tool_calls):
    return [
        call
        for call in tool_calls
        if call.status in {"created", "approved", "running", "awaiting_approval"}
    ]


def _immutable_effects(tool_calls):
    return tuple(
        {
            "tool_call_id": call.id,
            "agent_execution_id": call.agent_execution_id,
            "tool_name": call.tool_name,
            "side_effect_level": call.side_effect_level,
            "status": call.status,
            "output": deepcopy(call.output),
        }
        for call in tool_calls
        if call.status in {"completed", "succeeded"} and not _is_read_only(call.side_effect_level)
    )
