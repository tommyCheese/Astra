from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas.context_compaction import parse_child_checkpoint
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.executions import AgentExecutionRecord
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import RunEventRecord
from app.infrastructure.repositories.agent_executions import (
    TERMINAL_AGENT_STATUSES,
    AgentExecutionRepository,
)


@dataclass(frozen=True)
class SubagentRecoveryResult:
    resumable_execution_ids: tuple[str, ...] = ()
    replayable_execution_ids: tuple[str, ...] = ()
    unknown_execution_ids: tuple[str, ...] = ()
    incompatible_execution_ids: tuple[str, ...] = ()


class SubagentExecutionRecovery:
    """Reconciles stale child workers without replaying uncertain effects."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        stale_seconds: int = 45,
        runtime_version: str = "astra-subagent-v1",
    ):
        self.session = session
        self.stale_seconds = max(1, stale_seconds)
        self.runtime_version = runtime_version

    async def scan(self, run_id: str | None = None) -> SubagentRecoveryResult:
        repository = AgentExecutionRepository(self.session)
        stale = await repository.stale_active(heartbeat_before=utc_now() - timedelta(seconds=self.stale_seconds))
        resumable: list[str] = []
        replayable: list[str] = []
        unknown: list[str] = []
        incompatible: list[str] = []
        for execution in stale:
            if run_id is not None and execution.run_id != run_id:
                continue
            checkpoint = dict(execution.checkpoint or {})
            incompatibility = self._incompatibility(execution, checkpoint)
            if incompatibility:
                await self._fail_closed(execution, incompatibility)
                incompatible.append(execution.id)
                continue
            if execution.result is not None and execution.status == "completing":
                await self._complete_committed(execution)
                replayable.append(execution.id)
                continue
            uncertain_calls = list(
                (
                    await self.session.scalars(
                        select(ToolCallRecord).where(
                            ToolCallRecord.agent_execution_id == execution.id,
                            ToolCallRecord.status == "running",
                            ToolCallRecord.side_effect_level.not_in(["none", "read", "read_only"]),
                        )
                    )
                ).all()
            )
            if uncertain_calls:
                call_ids = [call.id for call in uncertain_calls]
                await self.session.execute(
                    update(ToolCallRecord)
                    .where(ToolCallRecord.id.in_(call_ids))
                    .values(
                        status="result_unknown",
                        completed_at=utc_now(),
                        error={"category": "non_idempotent_result_unknown"},
                    )
                )
                await self._wait_unknown(execution, call_ids)
                unknown.append(execution.id)
                continue
            await self._resume(execution)
            resumable.append(execution.id)
        await self.session.flush()
        return SubagentRecoveryResult(
            tuple(resumable),
            tuple(replayable),
            tuple(unknown),
            tuple(incompatible),
        )

    def _incompatibility(self, execution: AgentExecutionRecord, checkpoint: dict) -> str | None:
        if checkpoint.get("resume_safe") is False:
            return "checkpoint_marked_unsafe"
        if checkpoint.get("schema_version", 1) != 1:
            return "checkpoint_schema_drift"
        if checkpoint.get("runtime_version", self.runtime_version) != self.runtime_version:
            return "runtime_code_version_drift"
        snapshot = execution.catalog_snapshot or {}
        if checkpoint.get("tool_catalog_digest", snapshot.get("tool_digest")) != snapshot.get("tool_digest"):
            return "tool_catalog_version_drift"
        if checkpoint.get("skill_catalog_digest", snapshot.get("skill_digest")) != snapshot.get("skill_digest"):
            return "skill_catalog_version_drift"
        raw_context = checkpoint.get("context_checkpoint")
        if raw_context is not None:
            try:
                parse_child_checkpoint(raw_context)
            except ValueError:
                return "context_checkpoint_incompatible"
        return None

    async def _resume(self, execution: AgentExecutionRecord) -> None:
        checkpoint = {
            **(execution.checkpoint or {}),
            "event_cursor": await self._event_cursor(execution),
            "recovered_from_fencing_token": execution.fencing_token,
        }
        await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution.id,
                AgentExecutionRecord.state_version == execution.state_version,
                AgentExecutionRecord.cancellation_epoch == execution.cancellation_epoch,
                AgentExecutionRecord.status.not_in(TERMINAL_AGENT_STATUSES),
            )
            .values(
                status="queued",
                phase="recovery_resume",
                wait_reason="worker_lease_expired",
                checkpoint=checkpoint,
                worker_id=None,
                fencing_token=AgentExecutionRecord.fencing_token + 1,
                state_version=AgentExecutionRecord.state_version + 1,
                heartbeat_at=utc_now(),
                updated_at=utc_now(),
            )
        )

    async def _complete_committed(self, execution: AgentExecutionRecord) -> None:
        await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution.id,
                AgentExecutionRecord.state_version == execution.state_version,
                AgentExecutionRecord.status == "completing",
            )
            .values(
                status="completed",
                phase="terminal",
                worker_id=None,
                fencing_token=AgentExecutionRecord.fencing_token + 1,
                state_version=AgentExecutionRecord.state_version + 1,
                finished_at=utc_now(),
                updated_at=utc_now(),
            )
        )

    async def _wait_unknown(self, execution: AgentExecutionRecord, tool_call_ids: list[str]) -> None:
        await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution.id,
                AgentExecutionRecord.state_version == execution.state_version,
            )
            .values(
                status="waiting_resource",
                phase="result_unknown",
                wait_reason="non_idempotent_result_unknown",
                checkpoint={
                    **(execution.checkpoint or {}),
                    "result_unknown_tool_call_ids": tool_call_ids,
                },
                worker_id=None,
                fencing_token=AgentExecutionRecord.fencing_token + 1,
                state_version=AgentExecutionRecord.state_version + 1,
                updated_at=utc_now(),
            )
        )

    async def _fail_closed(self, execution: AgentExecutionRecord, reason: str) -> None:
        await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution.id,
                AgentExecutionRecord.state_version == execution.state_version,
            )
            .values(
                status="failed",
                phase="terminal",
                wait_reason=reason,
                error={"category": "incompatible_checkpoint", "reason": reason},
                worker_id=None,
                fencing_token=AgentExecutionRecord.fencing_token + 1,
                state_version=AgentExecutionRecord.state_version + 1,
                finished_at=utc_now(),
                updated_at=utc_now(),
            )
        )

    async def _event_cursor(self, execution: AgentExecutionRecord) -> int:
        return int(
            await self.session.scalar(
                select(RunEventRecord.id)
                .where(RunEventRecord.agent_execution_id == execution.id)
                .order_by(RunEventRecord.id.desc())
                .limit(1)
            )
            or 0
        )
