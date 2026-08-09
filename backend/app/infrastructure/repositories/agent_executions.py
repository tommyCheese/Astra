from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas.subagents import DelegationContract, SubagentExecutionStatus
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.executions import AgentExecutionRecord
from app.infrastructure.db.models.runs import RunRecord


class AgentExecutionStateError(RuntimeError):
    pass


TERMINAL_AGENT_STATUSES = frozenset(
    {
        SubagentExecutionStatus.completed.value,
        SubagentExecutionStatus.completed_with_warnings.value,
        SubagentExecutionStatus.blocked.value,
        SubagentExecutionStatus.failed.value,
        SubagentExecutionStatus.cancelled.value,
    }
)

ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"authorizing", "queued", "blocked", "cancelled"}),
    "authorizing": frozenset({"queued", "blocked", "failed", "cancelled"}),
    "queued": frozenset({"running", "blocked", "cancelled"}),
    "running": frozenset(
        {
            "waiting_parent",
            "waiting_approval",
            "waiting_resource",
            "completing",
            "blocked",
            "failed",
            "cancelled",
        }
    ),
    "waiting_parent": frozenset({"queued", "running", "blocked", "failed", "cancelled"}),
    "waiting_approval": frozenset({"queued", "running", "blocked", "failed", "cancelled"}),
    "waiting_resource": frozenset({"queued", "running", "blocked", "failed", "cancelled"}),
    "completing": frozenset({"completed", "completed_with_warnings", "blocked", "failed", "cancelled"}),
}


@dataclass
class AgentExecutionRepository:
    session: AsyncSession

    async def require(self, execution_id: str) -> AgentExecutionRecord:
        execution = await self.session.get(AgentExecutionRecord, execution_id)
        if execution is None:
            raise ValueError(f"AgentExecution not found: {execution_id}")
        return execution

    async def _refreshed(self, execution_id: str) -> AgentExecutionRecord:
        execution = await self.require(execution_id)
        await self.session.refresh(execution)
        return execution

    async def root_for_run(self, run_id: str) -> AgentExecutionRecord | None:
        return await self.session.scalar(
            select(AgentExecutionRecord).where(
                AgentExecutionRecord.run_id == run_id,
                AgentExecutionRecord.root_slot == "root",
            )
        )

    async def get_or_create_root(self, run_id: str) -> AgentExecutionRecord:
        existing = await self.root_for_run(run_id)
        if existing is not None:
            return existing
        run = await self.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        root = self.root_from_run(run)
        try:
            async with self.session.begin_nested():
                self.session.add(root)
                await self.session.flush()
        except IntegrityError:
            existing = await self.root_for_run(run_id)
            if existing is None:
                raise
            return existing
        return root

    @staticmethod
    def root_from_run(run: RunRecord) -> AgentExecutionRecord:
        subagent_budgets = (((run.reasoning_policy or {}).get("effective") or {}).get("subagents") or {}).get("budgets") or {}
        return AgentExecutionRecord(
            run_id=run.id,
            task_id=run.task_id,
            execution_type="root",
            root_slot="root",
            request_id="root",
            depth=0,
            ordinal=0,
            contract=deepcopy(run.task_contract or {}),
            context_manifest={},
            catalog_snapshot={},
            budget_envelope=deepcopy(subagent_budgets),
            budget_usage=deepcopy((run.agent_state or {}).get("budget_usage") or {}),
            status=_root_status(run.status),
            phase=_root_phase(run.status),
            checkpoint=deepcopy(run.agent_state or {}),
            result=deepcopy(run.result),
            created_at=run.created_at,
            queued_at=run.created_at,
            claimed_at=run.started_at,
            heartbeat_at=run.completed_at or run.started_at,
            finished_at=run.completed_at,
            updated_at=run.updated_at,
        )

    async def sync_root_from_run(self, run: RunRecord) -> AgentExecutionRecord:
        root = await self.get_or_create_root(run.id)
        root.contract = deepcopy(run.task_contract or {})
        root.checkpoint = deepcopy(run.agent_state or {})
        root.budget_usage = deepcopy((run.agent_state or {}).get("budget_usage") or {})
        root.status = _root_status(run.status)
        root.phase = _root_phase(run.status)
        root.result = deepcopy(run.result)
        root.heartbeat_at = run.completed_at or run.started_at or root.heartbeat_at
        root.finished_at = run.completed_at
        root.state_version += 1
        root.updated_at = utc_now()
        await self.session.flush()
        return root

    async def create_child(
        self,
        *,
        contract: DelegationContract,
        identity_id: str | None = None,
        delegation_id: str | None = None,
        parent_node_execution_id: str | None = None,
        context_manifest: dict[str, Any] | None = None,
        catalog_snapshot: dict[str, Any] | None = None,
    ) -> AgentExecutionRecord:
        request_id = contract.request.request_id
        existing = await self.session.scalar(
            select(AgentExecutionRecord).where(
                AgentExecutionRecord.parent_execution_id == contract.parent_execution_id,
                AgentExecutionRecord.request_id == request_id,
            )
        )
        if existing is not None:
            if existing.contract != contract.model_dump(mode="json"):
                raise AgentExecutionStateError("Delegation request id already exists with a different contract")
            return existing
        parent = await self.require(contract.parent_execution_id)
        if parent.run_id != contract.run_id or parent.task_id != contract.task_id:
            raise AgentExecutionStateError("Delegation contract crosses the parent Run boundary")
        if parent.status in TERMINAL_AGENT_STATUSES:
            raise AgentExecutionStateError("Cannot delegate from a terminal AgentExecution")
        if contract.depth != parent.depth + 1:
            raise AgentExecutionStateError("Delegation depth does not follow the parent")
        ordinal = (
            int(
                await self.session.scalar(
                    select(func.count(AgentExecutionRecord.id)).where(AgentExecutionRecord.parent_execution_id == parent.id)
                )
                or 0
            )
            + 1
        )
        child = AgentExecutionRecord(
            run_id=contract.run_id,
            task_id=contract.task_id,
            parent_execution_id=parent.id,
            parent_node_execution_id=parent_node_execution_id,
            identity_id=identity_id,
            delegation_id=delegation_id,
            execution_type="child",
            root_slot=None,
            request_id=request_id,
            depth=contract.depth,
            ordinal=ordinal,
            contract=contract.model_dump(mode="json"),
            context_manifest=deepcopy(context_manifest or {}),
            catalog_snapshot=deepcopy(catalog_snapshot or {}),
            budget_envelope=contract.request.budget.model_dump(mode="json"),
            budget_usage={},
            status="queued",
            phase="proposed",
            checkpoint={},
            created_at=contract.created_at,
            queued_at=contract.created_at,
            updated_at=contract.created_at,
        )
        try:
            async with self.session.begin_nested():
                self.session.add(child)
                await self.session.flush()
        except IntegrityError as exc:
            existing = await self.session.scalar(
                select(AgentExecutionRecord).where(
                    AgentExecutionRecord.parent_execution_id == parent.id,
                    AgentExecutionRecord.request_id == request_id,
                )
            )
            if existing is None:
                raise
            if existing.contract != contract.model_dump(mode="json"):
                raise AgentExecutionStateError("Delegation request id already exists with a different contract") from exc
            return existing
        return child

    async def transition(
        self,
        execution_id: str,
        *,
        expected_state_version: int,
        status: SubagentExecutionStatus | str,
        phase: str,
        wait_reason: str | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        expected_fencing_token: int | None = None,
        expected_cancellation_epoch: int | None = None,
    ) -> AgentExecutionRecord:
        current = await self.require(execution_id)
        target_status = status.value if isinstance(status, SubagentExecutionStatus) else str(status)
        if current.status == target_status:
            raise AgentExecutionStateError("AgentExecution transition must change status")
        if target_status not in ALLOWED_STATUS_TRANSITIONS.get(current.status, frozenset()):
            raise AgentExecutionStateError(f"Illegal AgentExecution transition: {current.status} -> {target_status}")
        conditions = [
            AgentExecutionRecord.id == execution_id,
            AgentExecutionRecord.state_version == expected_state_version,
        ]
        if expected_fencing_token is not None:
            conditions.append(AgentExecutionRecord.fencing_token == expected_fencing_token)
        if expected_cancellation_epoch is not None:
            conditions.append(AgentExecutionRecord.cancellation_epoch == expected_cancellation_epoch)
        values: dict[str, Any] = {
            "status": target_status,
            "phase": phase,
            "wait_reason": wait_reason,
            "state_version": expected_state_version + 1,
            "updated_at": utc_now(),
        }
        if result is not None:
            values["result"] = deepcopy(result)
        if error is not None:
            values["error"] = deepcopy(error)
        if target_status in TERMINAL_AGENT_STATUSES:
            values["finished_at"] = utc_now()
            values["worker_id"] = None
        elif target_status in {
            "queued",
            "waiting_parent",
            "waiting_approval",
            "waiting_resource",
        }:
            values["worker_id"] = None
        statement = update(AgentExecutionRecord).where(*conditions).values(**values)
        outcome = await self.session.execute(statement)
        if outcome.rowcount != 1:
            raise AgentExecutionStateError("Stale AgentExecution transition")
        await self.session.flush()
        return await self._refreshed(execution_id)

    async def claim(
        self,
        execution_id: str,
        *,
        worker_id: str,
        expected_state_version: int,
        expected_cancellation_epoch: int | None = None,
    ) -> AgentExecutionRecord:
        current = await self.require(execution_id)
        run = await self.session.get(RunRecord, current.run_id)
        if run is None or run.status == "cancelled":
            raise AgentExecutionStateError("AgentExecution belongs to a cancelled Run")
        if current.cancellation_epoch != run.cancellation_epoch:
            raise AgentExecutionStateError("AgentExecution cancellation epoch is stale")
        if expected_cancellation_epoch is not None and current.cancellation_epoch != expected_cancellation_epoch:
            raise AgentExecutionStateError("AgentExecution cancellation epoch is stale")
        now = utc_now()
        statement = (
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.status == "queued",
                AgentExecutionRecord.state_version == expected_state_version,
                AgentExecutionRecord.worker_id.is_(None),
                AgentExecutionRecord.cancellation_epoch == run.cancellation_epoch,
            )
            .values(
                status="running",
                phase="claimed",
                worker_id=worker_id,
                fencing_token=AgentExecutionRecord.fencing_token + 1,
                state_version=expected_state_version + 1,
                claimed_at=now,
                heartbeat_at=now,
                updated_at=now,
            )
        )
        outcome = await self.session.execute(statement)
        if outcome.rowcount != 1:
            raise AgentExecutionStateError("AgentExecution claim lost or is stale")
        await self.session.flush()
        return await self._refreshed(execution_id)

    async def heartbeat(
        self,
        execution_id: str,
        *,
        worker_id: str,
        fencing_token: int,
        cancellation_epoch: int | None = None,
    ) -> AgentExecutionRecord:
        now = utc_now()
        outcome = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.worker_id == worker_id,
                AgentExecutionRecord.fencing_token == fencing_token,
                AgentExecutionRecord.status.not_in(TERMINAL_AGENT_STATUSES),
                *([AgentExecutionRecord.cancellation_epoch == cancellation_epoch] if cancellation_epoch is not None else []),
            )
            .values(heartbeat_at=now, updated_at=now)
        )
        if outcome.rowcount != 1:
            raise AgentExecutionStateError("Stale AgentExecution heartbeat")
        await self.session.flush()
        return await self._refreshed(execution_id)

    async def save_checkpoint(
        self,
        execution_id: str,
        *,
        worker_id: str,
        fencing_token: int,
        expected_state_version: int,
        checkpoint: dict[str, Any],
        budget_usage: dict[str, Any] | None = None,
        cancellation_epoch: int | None = None,
    ) -> AgentExecutionRecord:
        values: dict[str, Any] = {
            "checkpoint": deepcopy(checkpoint),
            "heartbeat_at": utc_now(),
            "updated_at": utc_now(),
            "state_version": expected_state_version + 1,
        }
        if budget_usage is not None:
            values["budget_usage"] = deepcopy(budget_usage)
        outcome = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == execution_id,
                AgentExecutionRecord.worker_id == worker_id,
                AgentExecutionRecord.fencing_token == fencing_token,
                AgentExecutionRecord.state_version == expected_state_version,
                AgentExecutionRecord.status.not_in(TERMINAL_AGENT_STATUSES),
                *([AgentExecutionRecord.cancellation_epoch == cancellation_epoch] if cancellation_epoch is not None else []),
            )
            .values(**values)
        )
        if outcome.rowcount != 1:
            raise AgentExecutionStateError("Stale AgentExecution checkpoint")
        await self.session.flush()
        return await self._refreshed(execution_id)

    async def descendants(self, execution_id: str) -> list[AgentExecutionRecord]:
        root = await self.require(execution_id)
        executions = list(
            (
                await self.session.scalars(
                    select(AgentExecutionRecord)
                    .where(AgentExecutionRecord.run_id == root.run_id)
                    .order_by(AgentExecutionRecord.depth, AgentExecutionRecord.ordinal)
                )
            ).all()
        )
        pending = {execution_id}
        descendants: list[AgentExecutionRecord] = []
        for execution in executions:
            if execution.parent_execution_id in pending:
                descendants.append(execution)
                pending.add(execution.id)
        return descendants

    async def active_descendants(self, execution_id: str) -> list[AgentExecutionRecord]:
        return [
            execution for execution in await self.descendants(execution_id) if execution.status not in TERMINAL_AGENT_STATUSES
        ]

    async def stale_active(
        self,
        *,
        heartbeat_before: datetime,
        limit: int = 100,
    ) -> list[AgentExecutionRecord]:
        return list(
            (
                await self.session.scalars(
                    select(AgentExecutionRecord)
                    .where(
                        AgentExecutionRecord.status.not_in(TERMINAL_AGENT_STATUSES),
                        AgentExecutionRecord.worker_id.is_not(None),
                        AgentExecutionRecord.heartbeat_at < heartbeat_before,
                    )
                    .order_by(AgentExecutionRecord.heartbeat_at, AgentExecutionRecord.id)
                    .limit(limit)
                )
            ).all()
        )


def _root_status(run_status: str) -> str:
    if run_status in TERMINAL_AGENT_STATUSES:
        return run_status
    if run_status == "waiting_user":
        return "waiting_parent"
    if run_status in {"created", "planning"}:
        return "queued"
    return "running"


def _root_phase(run_status: str) -> str:
    if run_status in TERMINAL_AGENT_STATUSES:
        return "terminal"
    if run_status == "waiting_user":
        return "waiting_parent"
    if run_status in {"created", "planning"}:
        return "planning"
    return "executing"
