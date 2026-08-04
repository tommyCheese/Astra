from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.model_base import utc_now
from app.db.models.executions import AgentBudgetReservationRecord, AgentExecutionRecord
from app.repositories.agent_executions import TERMINAL_AGENT_STATUSES
from app.schemas.subagents import SubagentBudgetEnvelope


class HierarchicalBudgetError(RuntimeError):
    pass


BUDGET_FIELDS = (
    "tokens",
    "model_calls",
    "tool_calls",
    "wall_time_ms",
    "cost_usd",
    "children",
)


def envelope_amounts(value: SubagentBudgetEnvelope | dict[str, Any]) -> dict[str, float]:
    raw = value.model_dump() if isinstance(value, SubagentBudgetEnvelope) else value
    return {
        "tokens": float(raw.get("max_tokens", 0)),
        "model_calls": float(raw.get("max_model_calls", 0)),
        "tool_calls": float(raw.get("max_tool_calls", 0)),
        "wall_time_ms": float(
            raw.get("max_wall_time_ms", float(raw.get("max_wall_time_seconds", 0)) * 1000)
        ),
        "cost_usd": float(raw.get("max_cost_usd", 0)),
        "children": float(max(1, int(raw.get("max_children", raw.get("max_children_total", 1))))),
    }


class HierarchicalBudgetManager:
    """CAS-backed parent reservation and exact-once child settlement."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        parent_reserve: dict[str, float] | None = None,
    ):
        self.session = session
        self.parent_reserve = {key: float(value) for key, value in (parent_reserve or {}).items()}

    async def reserve(
        self,
        *,
        parent_execution_id: str,
        child_execution_id: str,
        envelope: SubagentBudgetEnvelope | dict[str, Any],
        max_children_total: int,
        max_children_per_parent: int,
        max_parallel_children: int,
        commit: bool = True,
    ) -> AgentBudgetReservationRecord:
        existing = await self.session.scalar(
            select(AgentBudgetReservationRecord).where(
                AgentBudgetReservationRecord.child_execution_id == child_execution_id
            )
        )
        requested = envelope_amounts(envelope)
        if existing is not None:
            if existing.envelope != requested:
                raise HierarchicalBudgetError("Child budget reservation is immutable")
            return existing
        parent, child = await self._budget_lineage(parent_execution_id, child_execution_id)
        # A previous reservation in the same fan-out transaction may have
        # advanced the parent's CAS version through a SQL UPDATE.
        await self.session.refresh(parent)
        run_children, direct_children, active_children = await self._child_counts(parent)
        self._validate_child_counts(
            run_children,
            direct_children,
            active_children,
            max_children_total,
            max_children_per_parent,
            max_parallel_children,
        )
        active = list(
            (
                await self.session.scalars(
                    select(AgentBudgetReservationRecord).where(
                        AgentBudgetReservationRecord.parent_execution_id == parent.id,
                        AgentBudgetReservationRecord.status == "reserved",
                    )
                )
            ).all()
        )
        reserved = {
            key: sum(float(item.envelope.get(key, 0)) for item in active) for key in BUDGET_FIELDS
        }
        limits = envelope_amounts(parent.budget_envelope or {})
        # A historical root may not carry subagent limits; fail closed rather
        # than treating absent limits as unlimited.
        shortfalls = self._budget_shortfalls(parent, requested, reserved, limits)
        if shortfalls:
            raise HierarchicalBudgetError(f"Parent budget cannot fund child: {shortfalls}")
        usage = deepcopy(parent.budget_usage or {})
        usage["delegated_reserved"] = {key: reserved[key] + requested[key] for key in BUDGET_FIELDS}
        outcome = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == parent.id,
                AgentExecutionRecord.state_version == parent.state_version,
            )
            .values(
                budget_usage=usage,
                state_version=parent.state_version + 1,
                updated_at=utc_now(),
            )
        )
        if outcome.rowcount != 1:
            raise HierarchicalBudgetError("Parent budget changed during reservation")
        reservation = AgentBudgetReservationRecord(
            run_id=parent.run_id,
            parent_execution_id=parent.id,
            child_execution_id=child.id,
            envelope=requested,
            parent_reserve=deepcopy(self.parent_reserve),
            actual_usage={},
            returned_budget={},
            status="reserved",
        )
        try:
            async with self.session.begin_nested():
                self.session.add(reservation)
                await self.session.flush()
        except IntegrityError as exc:
            existing = await self.session.scalar(
                select(AgentBudgetReservationRecord).where(
                    AgentBudgetReservationRecord.child_execution_id == child.id
                )
            )
            if existing is None or existing.envelope != requested:
                raise HierarchicalBudgetError(
                    "Concurrent child budget reservation conflict"
                ) from exc
            return existing
        if commit:
            await self.session.commit()
        return reservation

    async def _child_counts(self, parent):
        async def count(*conditions):
            value = await self.session.scalar(
                select(func.count(AgentExecutionRecord.id)).where(*conditions)
            )
            return int(value or 0)

        return (
            await count(
                AgentExecutionRecord.run_id == parent.run_id,
                AgentExecutionRecord.parent_execution_id.is_not(None),
            ),
            await count(AgentExecutionRecord.parent_execution_id == parent.id),
            await count(
                AgentExecutionRecord.parent_execution_id == parent.id,
                AgentExecutionRecord.status.not_in(TERMINAL_AGENT_STATUSES),
            ),
        )

    async def _budget_lineage(self, parent_execution_id, child_execution_id):
        parent = await self.session.get(AgentExecutionRecord, parent_execution_id)
        child = await self.session.get(AgentExecutionRecord, child_execution_id)
        if parent is None or child is None or child.parent_execution_id != parent.id:
            raise HierarchicalBudgetError("Budget reservation must follow Agent lineage")
        if child.depth != parent.depth + 1:
            raise HierarchicalBudgetError("Child budget depth is invalid")
        return parent, child

    @staticmethod
    def _validate_child_counts(
        run_children,
        direct_children,
        active_children,
        max_children_total,
        max_children_per_parent,
        max_parallel_children,
    ) -> None:
        if run_children > max_children_total or direct_children > max_children_per_parent:
            raise HierarchicalBudgetError("Child count budget is exhausted")
        if active_children > max_parallel_children:
            raise HierarchicalBudgetError("Parallel child budget is exhausted")

    def _budget_shortfalls(self, parent, requested, reserved, limits):
        shortfalls = {}
        own_usage = parent.budget_usage or {}
        descendants = own_usage.get("descendant_usage") or {}
        for key in BUDGET_FIELDS:
            reserve = float(self.parent_reserve.get(key, 0))
            consumed = float(own_usage.get(key, 0)) + float(descendants.get(key, 0))
            available = limits[key] - reserve - consumed - reserved[key]
            if requested[key] > max(0.0, available):
                shortfalls[key] = {
                    "requested": requested[key],
                    "available": max(0.0, available),
                    "parent_reserve": reserve,
                }
        return shortfalls

    async def settle(
        self,
        child_execution_id: str,
        *,
        actual_usage: dict[str, int | float],
        commit: bool = True,
    ) -> AgentBudgetReservationRecord:
        reservation = await self.session.scalar(
            select(AgentBudgetReservationRecord).where(
                AgentBudgetReservationRecord.child_execution_id == child_execution_id
            )
        )
        if reservation is None:
            raise HierarchicalBudgetError("Child budget reservation does not exist")
        normalized = self._normalized_actual_usage(actual_usage)
        if reservation.status == "settled":
            if reservation.actual_usage != normalized:
                raise HierarchicalBudgetError("Child budget was already settled differently")
            return reservation
        self._validate_reserved_usage(reservation, normalized)
        parent = await self.session.get(AgentExecutionRecord, reservation.parent_execution_id)
        child = await self.session.get(AgentExecutionRecord, child_execution_id)
        if parent is None or child is None:
            raise HierarchicalBudgetError("Budget lineage is unavailable")
        returned, usage = self._settled_parent_usage(parent, reservation, normalized)
        changed = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == parent.id,
                AgentExecutionRecord.state_version == parent.state_version,
            )
            .values(
                budget_usage=usage,
                state_version=parent.state_version + 1,
                updated_at=utc_now(),
            )
        )
        if changed.rowcount != 1:
            raise HierarchicalBudgetError("Parent budget changed during settlement")
        reservation.actual_usage = normalized
        reservation.returned_budget = returned
        reservation.status = "settled"
        reservation.state_version += 1
        reservation.settled_at = utc_now()
        child.budget_usage = {**(child.budget_usage or {}), **normalized}
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return reservation

    @staticmethod
    def _normalized_actual_usage(actual_usage):
        normalized = {key: float(actual_usage.get(key, 0)) for key in BUDGET_FIELDS}
        normalized["children"] = 1.0
        return normalized

    @staticmethod
    def _validate_reserved_usage(reservation, normalized) -> None:
        exceeded = {
            key: {"actual": normalized[key], "reserved": reservation.envelope.get(key, 0)}
            for key in BUDGET_FIELDS
            if normalized[key] > float(reservation.envelope.get(key, 0))
        }
        if exceeded:
            raise HierarchicalBudgetError(f"Child exceeded reserved budget: {exceeded}")

    @staticmethod
    def _settled_parent_usage(parent, reservation, normalized):
        returned = {
            key: float(reservation.envelope.get(key, 0)) - normalized[key] for key in BUDGET_FIELDS
        }
        usage = deepcopy(parent.budget_usage or {})
        active = dict(usage.get("delegated_reserved") or {})
        usage["delegated_reserved"] = {
            key: max(0.0, float(active.get(key, 0)) - float(reservation.envelope.get(key, 0)))
            for key in BUDGET_FIELDS
        }
        descendant = dict(usage.get("descendant_usage") or {})
        usage["descendant_usage"] = {
            key: float(descendant.get(key, 0)) + normalized[key] for key in BUDGET_FIELDS
        }
        return returned, usage


@dataclass(frozen=True)
class DelegationGateInput:
    complexity: float
    independence: float
    context_pressure: float
    estimated_benefit: float
    write_conflict_risk: float
    execution_risk: float
    budget_fraction_remaining: float
    simple_atomic: bool = False
    strongly_sequential: bool = False


@dataclass(frozen=True)
class DelegationGateDecision:
    allowed: bool
    score: float
    reason_code: str
    diagnostics: dict[str, float | bool]


def evaluate_delegation(item: DelegationGateInput) -> DelegationGateDecision:
    score = (
        0.22 * item.complexity
        + 0.28 * item.independence
        + 0.12 * item.context_pressure
        + 0.28 * item.estimated_benefit
        + 0.10 * item.budget_fraction_remaining
        - 0.22 * item.write_conflict_risk
        - 0.18 * item.execution_risk
    )
    if item.simple_atomic:
        allowed, reason = False, "delegation_not_beneficial_simple"
    elif item.strongly_sequential:
        allowed, reason = False, "delegation_not_beneficial_sequential"
    elif item.budget_fraction_remaining < 0.25:
        allowed, reason = False, "delegation_budget_low"
    elif item.write_conflict_risk >= 0.6:
        allowed, reason = False, "delegation_write_conflict"
    elif score < 0.45:
        allowed, reason = False, "delegation_not_beneficial"
    else:
        allowed, reason = True, "delegation_beneficial"
    return DelegationGateDecision(
        allowed=allowed,
        score=round(score, 6),
        reason_code=reason,
        diagnostics=dict(item.__dict__),
    )
