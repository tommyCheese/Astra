from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    PlanEdgeRecord,
    PlanNodeRecord,
    PlanRecord,
    RunRecord,
    utc_now,
)
from app.schemas.agent import (
    ExpectedObservation,
    PlanDraft,
    PlanNodeStatus,
    PlanNodeView,
    PlanStatus,
    PlanView,
)


NODE_TRANSITIONS: dict[str, set[str]] = {
    PlanNodeStatus.pending.value: {
        PlanNodeStatus.running.value,
        PlanNodeStatus.blocked.value,
        PlanNodeStatus.skipped.value,
    },
    PlanNodeStatus.running.value: {
        PlanNodeStatus.completed.value,
        PlanNodeStatus.failed.value,
        PlanNodeStatus.blocked.value,
    },
    PlanNodeStatus.failed.value: {PlanNodeStatus.blocked.value},
    PlanNodeStatus.completed.value: set(),
    PlanNodeStatus.blocked.value: set(),
    PlanNodeStatus.skipped.value: set(),
}


class PlanStateError(ValueError):
    pass


class PlanRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        run_id: str,
        draft: PlanDraft,
        *,
        status: PlanStatus = PlanStatus.active,
        supersedes_plan_id: str | None = None,
        lineage: dict[str, str] | None = None,
    ) -> PlanRecord:
        run = await self.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        next_version = int(
            (
                await self.session.scalar(
                    select(func.max(PlanRecord.version)).where(PlanRecord.run_id == run_id)
                )
            )
            or 0
        ) + 1
        plan = PlanRecord(
            run_id=run_id,
            version=next_version,
            strategy=draft.strategy.value,
            status=status.value,
            supersedes_plan_id=supersedes_plan_id,
            activated_at=utc_now() if status == PlanStatus.active else None,
        )
        self.session.add(plan)
        await self.session.flush()
        nodes_by_key: dict[str, PlanNodeRecord] = {}
        lineage = lineage or {}
        for index, item in enumerate(draft.nodes, start=1):
            node = PlanNodeRecord(
                plan_id=plan.id,
                node_key=item.node_key,
                index=index,
                title=item.title,
                intent=item.intent,
                status=PlanNodeStatus.pending.value,
                required_capabilities=list(item.required_capabilities),
                success_criteria_refs=list(item.success_criteria_refs),
                expected_outcome=item.expected_outcome.model_dump(mode="json"),
                risk_level=item.risk_level,
                optional=item.optional,
                lineage_node_id=lineage.get(item.node_key),
            )
            self.session.add(node)
            nodes_by_key[item.node_key] = node
        await self.session.flush()
        for item in draft.nodes:
            for predecessor_key in item.depends_on:
                self.session.add(
                    PlanEdgeRecord(
                        plan_id=plan.id,
                        predecessor_id=nodes_by_key[predecessor_key].id,
                        successor_id=nodes_by_key[item.node_key].id,
                        dependency_type="hard",
                    )
                )
        if status == PlanStatus.active:
            if run.active_plan_id:
                previous = await self.session.get(PlanRecord, run.active_plan_id)
                if previous and previous.status == PlanStatus.active.value:
                    previous.status = PlanStatus.superseded.value
            run.active_plan_id = plan.id
        run.updated_at = utc_now()
        await self.session.flush()
        loaded = await self.require(plan.id)
        await self._event(
            run_id,
            "plan.created",
            {
                "plan_id": plan.id,
                "version": plan.version,
                "strategy": plan.strategy,
                "status": plan.status,
                "node_count": len(draft.nodes),
                "supersedes_plan_id": supersedes_plan_id,
            },
        )
        return loaded

    async def require(self, plan_id: str) -> PlanRecord:
        result = await self.session.execute(
            select(PlanRecord)
            .where(PlanRecord.id == plan_id)
            .options(selectinload(PlanRecord.nodes), selectinload(PlanRecord.edges))
            .execution_options(populate_existing=True)
        )
        plan = result.scalar_one_or_none()
        if plan is None:
            raise ValueError(f"Plan not found: {plan_id}")
        return plan

    async def active_for_run(self, run_id: str) -> PlanRecord | None:
        run = await self.session.get(RunRecord, run_id)
        if run is None or not run.active_plan_id:
            return None
        return await self.require(run.active_plan_id)

    async def require_node(self, node_id: str) -> PlanNodeRecord:
        node = await self.session.get(PlanNodeRecord, node_id)
        if node is None:
            raise ValueError(f"Plan node not found: {node_id}")
        return node

    async def transition_node(
        self,
        node_id: str,
        target: PlanNodeStatus,
        *,
        evidence_refs: Iterable[str] = (),
        failure: dict[str, Any] | None = None,
    ) -> PlanNodeRecord:
        node = await self.require_node(node_id)
        if target.value not in NODE_TRANSITIONS.get(node.status, set()):
            raise PlanStateError(f"Invalid plan node transition: {node.status} -> {target.value}")
        node.status = target.value
        if target == PlanNodeStatus.running and node.started_at is None:
            node.started_at = utc_now()
        if target in {
            PlanNodeStatus.completed,
            PlanNodeStatus.failed,
            PlanNodeStatus.blocked,
            PlanNodeStatus.skipped,
        }:
            node.completed_at = utc_now()
        if evidence_refs:
            node.evidence_refs = list(dict.fromkeys([*node.evidence_refs, *evidence_refs]))
        if failure is not None:
            node.failure = failure
        plan = await self.require(node.plan_id)
        await self._event(
            plan.run_id,
            "plan.node.updated",
            {
                "plan_id": plan.id,
                "plan_version": plan.version,
                "plan_node_id": node.id,
                "node_key": node.node_key,
                "status": node.status,
                "evidence_refs": node.evidence_refs,
                "failure": node.failure,
            },
        )
        await self.session.flush()
        return node

    async def activate(self, plan_id: str, *, expected_version: int | None = None) -> PlanRecord:
        plan = await self.require(plan_id)
        if expected_version is not None and plan.version != expected_version:
            raise PlanStateError(
                f"Plan version conflict: expected {expected_version}, got {plan.version}"
            )
        run = await self.session.get(RunRecord, plan.run_id)
        if run is None:
            raise ValueError(f"Run not found: {plan.run_id}")
        if run.active_plan_id and run.active_plan_id != plan.id:
            previous = await self.session.get(PlanRecord, run.active_plan_id)
            if previous:
                previous.status = PlanStatus.superseded.value
        plan.status = PlanStatus.active.value
        plan.activated_at = plan.activated_at or utc_now()
        run.active_plan_id = plan.id
        await self.session.flush()
        return plan

    async def _event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        from app.db.models import RunEventRecord

        self.session.add(RunEventRecord(run_id=run_id, type=event_type, payload=payload))


def plan_to_view(plan: PlanRecord) -> PlanView:
    nodes = sorted(plan.nodes, key=lambda item: item.index)
    node_by_id = {node.id: node for node in nodes}
    dependencies: dict[str, list[str]] = {node.id: [] for node in nodes}
    for edge in plan.edges:
        if edge.successor_id in dependencies and edge.predecessor_id in node_by_id:
            dependencies[edge.successor_id].append(node_by_id[edge.predecessor_id].node_key)
    return PlanView(
        id=plan.id,
        run_id=plan.run_id,
        version=plan.version,
        strategy=plan.strategy,
        status=plan.status,
        supersedes_plan_id=plan.supersedes_plan_id,
        nodes=[
            PlanNodeView(
                id=node.id,
                plan_id=plan.id,
                plan_version=plan.version,
                node_key=node.node_key,
                index=node.index,
                title=node.title,
                intent=node.intent,
                status=node.status,
                depends_on=dependencies[node.id],
                required_capabilities=node.required_capabilities or [],
                success_criteria_refs=node.success_criteria_refs or [],
                expected_outcome=ExpectedObservation.model_validate(node.expected_outcome)
                if node.expected_outcome
                else None,
                risk_level=node.risk_level,
                optional=node.optional,
                evidence_refs=node.evidence_refs or [],
                failure=node.failure,
            )
            for node in nodes
        ],
    )

