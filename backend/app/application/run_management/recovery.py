"""Recover stale root-node and delegated executions for persisted Runs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.subagents.recovery import SubagentExecutionRecovery
from app.common.schemas.agent.types import NodeExecutionPhase, NodeExecutionStatus
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.repositories.executions import NodeExecutionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


@dataclass(frozen=True)
class RunRecoveryScanResult:
    resumable_execution_ids: tuple[str, ...]
    replayable_execution_ids: tuple[str, ...]
    unknown_execution_ids: tuple[str, ...]
    resumable_agent_execution_ids: tuple[str, ...] = ()
    replayable_agent_execution_ids: tuple[str, ...] = ()
    unknown_agent_execution_ids: tuple[str, ...] = ()
    incompatible_agent_execution_ids: tuple[str, ...] = ()


class RunExecutionRecovery:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        stale_seconds: int = 45,
    ):
        self.session_factory = session_factory
        self.stale_seconds = max(1, stale_seconds)

    async def scan(self, run_id: str | None = None) -> RunRecoveryScanResult:
        resumable: list[str] = []
        replayable: list[str] = []
        unknown: list[str] = []
        async with self.session_factory() as session:
            repository = NodeExecutionRepository(session)
            stale = await repository.stale_active(
                heartbeat_before=utc_now() - timedelta(seconds=self.stale_seconds),
                phases=[
                    NodeExecutionPhase.claimed,
                    NodeExecutionPhase.running,
                    NodeExecutionPhase.waiting_resource,
                    NodeExecutionPhase.waiting_approval,
                    NodeExecutionPhase.committing,
                    NodeExecutionPhase.result_unknown,
                ],
            )
            for execution in stale:
                if run_id is not None and execution.run_id != run_id:
                    continue
                checkpoint = dict(execution.checkpoint or {})
                if execution.phase == NodeExecutionPhase.committing.value and execution.result:
                    replayable.append(execution.id)
                    continue
                if checkpoint.get("action_result") is not None:
                    replayable.append(execution.id)
                    continue
                if checkpoint.get("action_started") and not checkpoint.get(
                    "idempotent",
                    False,
                ):
                    updated = await repository.transition(
                        execution.id,
                        expected_version=execution.state_version,
                        phase=NodeExecutionPhase.result_unknown,
                        status=NodeExecutionStatus.waiting,
                        wait_reason="non_idempotent_result_unknown",
                        checkpoint=checkpoint,
                    )
                    await RunUnitOfWork(session).add_event(
                        execution.run_id,
                        "plan.node.result_unknown",
                        {
                            "node_execution_id": updated.id,
                            "plan_id": updated.plan_id,
                            "plan_version": updated.plan_version,
                            "plan_node_id": updated.plan_node_id,
                            "attempt": updated.attempt,
                            "dispatch_batch_id": updated.dispatch_batch_id,
                            "phase": updated.phase,
                            "status": updated.status,
                            "wait_reason": updated.wait_reason,
                        },
                    )
                    unknown.append(execution.id)
                    continue
                execution.heartbeat_at = utc_now()
                execution.worker_id = None
                execution.phase = NodeExecutionPhase.claimed.value
                execution.status = NodeExecutionStatus.active.value
                execution.wait_reason = "recovery_resume"
                execution.state_version += 1
                resumable.append(execution.id)
            agent_recovery = await SubagentExecutionRecovery(
                session,
                stale_seconds=self.stale_seconds,
            ).scan(run_id)
            await session.commit()
        return RunRecoveryScanResult(
            tuple(resumable),
            tuple(replayable),
            tuple(unknown),
            agent_recovery.resumable_execution_ids,
            agent_recovery.replayable_execution_ids,
            agent_recovery.unknown_execution_ids,
            agent_recovery.incompatible_execution_ids,
        )
