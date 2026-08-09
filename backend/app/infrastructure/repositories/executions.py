from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.schemas.agent.types import NodeExecutionPhase, NodeExecutionStatus
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.executions import (
    BudgetReservationRecord,
    NodeExecutionRecord,
    ResourceLeaseRecord,
)

TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        NodeExecutionStatus.completed.value,
        NodeExecutionStatus.failed.value,
        NodeExecutionStatus.cancelled.value,
        NodeExecutionStatus.blocked.value,
    }
)


class NodeExecutionStateError(ValueError):
    pass


@dataclass
class NodeExecutionRepository:
    session: AsyncSession

    async def require(self, execution_id: str) -> NodeExecutionRecord:
        result = await self.session.execute(
            select(NodeExecutionRecord)
            .where(NodeExecutionRecord.id == execution_id)
            .options(
                selectinload(NodeExecutionRecord.resource_leases),
                selectinload(NodeExecutionRecord.budget_reservations),
            )
            .execution_options(populate_existing=True)
        )
        execution = result.scalar_one_or_none()
        if execution is None:
            raise ValueError(f"NodeExecution not found: {execution_id}")
        return execution

    async def list_for_run(self, run_id: str) -> list[NodeExecutionRecord]:
        result = await self.session.execute(
            select(NodeExecutionRecord)
            .where(NodeExecutionRecord.run_id == run_id)
            .options(
                selectinload(NodeExecutionRecord.resource_leases),
                selectinload(NodeExecutionRecord.budget_reservations),
            )
            .order_by(NodeExecutionRecord.started_at, NodeExecutionRecord.id)
        )
        return list(result.scalars().all())

    async def active_for_run(self, run_id: str) -> list[NodeExecutionRecord]:
        result = await self.session.execute(
            select(NodeExecutionRecord)
            .where(
                NodeExecutionRecord.run_id == run_id,
                NodeExecutionRecord.status.in_([NodeExecutionStatus.active.value, NodeExecutionStatus.waiting.value]),
            )
            .order_by(NodeExecutionRecord.started_at, NodeExecutionRecord.id)
        )
        return list(result.scalars().all())

    async def create_claim(
        self,
        *,
        run_id: str,
        plan_id: str,
        plan_version: int,
        plan_node_id: str,
        dispatch_batch_id: str | None = None,
        worker_id: str | None = None,
        slot_index: int | None = None,
        agent_execution_id: str | None = None,
    ) -> NodeExecutionRecord:
        attempt = (
            int(
                await self.session.scalar(
                    select(func.max(NodeExecutionRecord.attempt)).where(NodeExecutionRecord.plan_node_id == plan_node_id)
                )
                or 0
            )
            + 1
        )
        execution = NodeExecutionRecord(
            run_id=run_id,
            agent_execution_id=agent_execution_id,
            plan_id=plan_id,
            plan_version=plan_version,
            plan_node_id=plan_node_id,
            attempt=attempt,
            dispatch_batch_id=dispatch_batch_id or str(uuid.uuid4()),
            worker_id=worker_id,
            phase=NodeExecutionPhase.claimed.value,
            status=NodeExecutionStatus.active.value,
            current_slot="current",
            slot_index=slot_index,
        )
        self.session.add(execution)
        await self.session.flush()
        return execution

    async def transition(
        self,
        execution_id: str,
        *,
        expected_version: int,
        phase: NodeExecutionPhase,
        status: NodeExecutionStatus | None = None,
        wait_reason: str | None = None,
        checkpoint: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        failure: dict[str, Any] | None = None,
    ) -> NodeExecutionRecord:
        target_status = status or _status_for_phase(phase)
        now = utc_now()
        terminal = target_status.value in TERMINAL_EXECUTION_STATUSES
        values: dict[str, Any] = {
            "phase": phase.value,
            "status": target_status.value,
            "wait_reason": wait_reason,
            "heartbeat_at": now,
            "updated_at": now,
            "state_version": expected_version + 1,
        }
        if checkpoint is not None:
            values["checkpoint"] = checkpoint
        if result is not None:
            values["result"] = result
        if failure is not None:
            values["failure"] = failure
        if terminal:
            values["current_slot"] = None
            values["slot_index"] = None
            values["finished_at"] = now
        elif phase in {
            NodeExecutionPhase.waiting_approval,
            NodeExecutionPhase.waiting_resource,
            NodeExecutionPhase.result_unknown,
        }:
            values["slot_index"] = None
        changed = await self.session.execute(
            update(NodeExecutionRecord)
            .where(
                NodeExecutionRecord.id == execution_id,
                NodeExecutionRecord.state_version == expected_version,
            )
            .values(**values)
        )
        if changed.rowcount != 1:
            raise NodeExecutionStateError("NodeExecution state version changed")
        await self.session.flush()
        return await self.require(execution_id)

    async def heartbeat(self, execution_id: str, *, expected_version: int) -> int:
        changed = await self.session.execute(
            update(NodeExecutionRecord)
            .where(
                NodeExecutionRecord.id == execution_id,
                NodeExecutionRecord.state_version == expected_version,
                NodeExecutionRecord.status.in_([NodeExecutionStatus.active.value, NodeExecutionStatus.waiting.value]),
            )
            .values(heartbeat_at=utc_now(), updated_at=utc_now())
        )
        if changed.rowcount != 1:
            raise NodeExecutionStateError("NodeExecution cannot be heartbeated")
        return expected_version

    async def acquire_slot(
        self,
        execution_id: str,
        *,
        expected_version: int,
        total_slots: int,
    ) -> NodeExecutionRecord:
        execution = await self.require(execution_id)
        if execution.state_version != expected_version:
            raise NodeExecutionStateError("NodeExecution state version changed")
        result = await self.session.execute(
            select(NodeExecutionRecord.slot_index).where(
                NodeExecutionRecord.run_id == execution.run_id,
                NodeExecutionRecord.slot_index.is_not(None),
                NodeExecutionRecord.id != execution_id,
            )
        )
        occupied = {int(value) for value in result.scalars().all() if value is not None}
        slot_index = next(
            (index for index in range(max(1, total_slots)) if index not in occupied),
            None,
        )
        if slot_index is None:
            raise NodeExecutionStateError("No parallel execution slot is available")
        changed = await self.session.execute(
            update(NodeExecutionRecord)
            .where(
                NodeExecutionRecord.id == execution_id,
                NodeExecutionRecord.state_version == expected_version,
            )
            .values(
                slot_index=slot_index,
                phase=NodeExecutionPhase.running.value,
                status=NodeExecutionStatus.active.value,
                wait_reason=None,
                state_version=expected_version + 1,
                heartbeat_at=utc_now(),
                updated_at=utc_now(),
            )
        )
        if changed.rowcount != 1:
            raise NodeExecutionStateError("NodeExecution state version changed")
        await self.session.flush()
        return await self.require(execution_id)

    async def create_lease(
        self,
        *,
        run_id: str,
        execution_id: str,
        resource_key: str,
        resource_summary: str,
        mode: str,
        ttl_seconds: int = 30,
    ) -> ResourceLeaseRecord:
        if mode not in {"read", "write", "exclusive"}:
            raise ValueError(f"Unsupported resource lease mode: {mode}")
        now = utc_now()
        fencing_token = (
            int(
                await self.session.scalar(
                    select(func.max(ResourceLeaseRecord.fencing_token)).where(ResourceLeaseRecord.resource_key == resource_key)
                )
                or 0
            )
            + 1
        )
        lease = ResourceLeaseRecord(
            run_id=run_id,
            node_execution_id=execution_id,
            resource_key=resource_key,
            resource_summary=resource_summary,
            mode=mode,
            fencing_token=fencing_token,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        self.session.add(lease)
        await self.session.flush()
        return lease

    async def release_leases(
        self,
        execution_id: str,
        *,
        reason: str,
    ) -> int:
        now = utc_now()
        changed = await self.session.execute(
            update(ResourceLeaseRecord)
            .where(
                ResourceLeaseRecord.node_execution_id == execution_id,
                ResourceLeaseRecord.released_at.is_(None),
            )
            .values(released_at=now, release_reason=reason)
        )
        await self.session.flush()
        return int(changed.rowcount or 0)

    async def renew_leases(
        self,
        execution_id: str,
        *,
        ttl_seconds: int = 30,
    ) -> int:
        now = utc_now()
        changed = await self.session.execute(
            update(ResourceLeaseRecord)
            .where(
                ResourceLeaseRecord.node_execution_id == execution_id,
                ResourceLeaseRecord.released_at.is_(None),
                ResourceLeaseRecord.expires_at > now,
            )
            .values(expires_at=now + timedelta(seconds=max(1, ttl_seconds)))
        )
        await self.session.flush()
        return int(changed.rowcount or 0)

    async def reserve_budgets(
        self,
        *,
        run_id: str,
        execution_id: str,
        reservations: dict[str, int],
    ) -> list[BudgetReservationRecord]:
        records = [
            BudgetReservationRecord(
                run_id=run_id,
                node_execution_id=execution_id,
                budget_kind=kind,
                reserved=amount,
                consumed=0,
                status="reserved",
            )
            for kind, amount in sorted(reservations.items())
            if amount > 0
        ]
        self.session.add_all(records)
        await self.session.flush()
        return records

    async def settle_budgets(
        self,
        execution_id: str,
        *,
        consumed: dict[str, int],
        status: str = "settled",
    ) -> list[BudgetReservationRecord]:
        records = await self._budget_reservations(execution_id)
        now = utc_now()
        for record in records:
            used = max(0, consumed.get(record.budget_kind, 0))
            if used > record.reserved:
                raise NodeExecutionStateError(f"Budget consumption exceeds reservation: {record.budget_kind}")
            record.consumed = used
            record.status = status
            record.settled_at = now
        await self.session.flush()
        return records

    async def stale_active(
        self,
        *,
        heartbeat_before,
        phases: Iterable[NodeExecutionPhase] | None = None,
    ) -> list[NodeExecutionRecord]:
        query = select(NodeExecutionRecord).where(
            NodeExecutionRecord.status.in_([NodeExecutionStatus.active.value, NodeExecutionStatus.waiting.value]),
            NodeExecutionRecord.heartbeat_at < heartbeat_before,
        )
        if phases:
            query = query.where(NodeExecutionRecord.phase.in_([phase.value for phase in phases]))
        result = await self.session.execute(query.order_by(NodeExecutionRecord.heartbeat_at, NodeExecutionRecord.id))
        return list(result.scalars().all())

    async def _budget_reservations(self, execution_id: str) -> list[BudgetReservationRecord]:
        result = await self.session.execute(
            select(BudgetReservationRecord)
            .where(BudgetReservationRecord.node_execution_id == execution_id)
            .order_by(BudgetReservationRecord.budget_kind)
        )
        return list(result.scalars().all())


def _status_for_phase(phase: NodeExecutionPhase) -> NodeExecutionStatus:
    if phase in {
        NodeExecutionPhase.waiting_approval,
        NodeExecutionPhase.waiting_resource,
        NodeExecutionPhase.result_unknown,
    }:
        return NodeExecutionStatus.waiting
    if phase == NodeExecutionPhase.completed:
        return NodeExecutionStatus.completed
    if phase == NodeExecutionPhase.failed:
        return NodeExecutionStatus.failed
    if phase == NodeExecutionPhase.cancelled:
        return NodeExecutionStatus.cancelled
    return NodeExecutionStatus.active
