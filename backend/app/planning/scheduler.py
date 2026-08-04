"""Ready-node selection and durable Plan scheduling."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update

from app.db.model_base import utc_now
from app.db.models.executions import AgentJoinRecord, BudgetReservationRecord, NodeExecutionRecord
from app.db.models.plans import PlanNodeRecord, PlanRecord
from app.db.models.runs import RunRecord
from app.planning.service import PlanValidationError
from app.repositories.executions import NodeExecutionRepository
from app.repositories.plans import PlanRepository
from app.schemas.agent.planning import ActiveExecutionSummary
from app.schemas.agent.types import (
    NodeExecutionPhase,
    PlanNodeStatus,
)


@dataclass(frozen=True)
class ReadyNodeCandidate:
    node: PlanNodeRecord
    dependency_rank: int


@dataclass(frozen=True)
class DispatchBatch:
    id: str
    plan_id: str
    plan_version: int
    executions: tuple[NodeExecutionRecord, ...]
    total_slots: int
    used_slots: int


class PlanScheduler:
    def __init__(
        self,
        repository: PlanRepository,
        *,
        server_max_parallel_nodes: int = 3,
        parallel_execution_enabled: bool = True,
        provider_concurrency_limit: int = 8,
        capability_concurrency_limit: int = 4,
        parallel_safe_capabilities: set[str] | None = None,
    ):
        self.repository = repository
        self.execution_repository = NodeExecutionRepository(repository.session)
        self.server_max_parallel_nodes = max(1, server_max_parallel_nodes)
        self.parallel_execution_enabled = parallel_execution_enabled
        self.provider_concurrency_limit = max(1, provider_concurrency_limit)
        self.capability_concurrency_limit = max(1, capability_concurrency_limit)
        self.parallel_safe_capabilities = parallel_safe_capabilities

    @staticmethod
    def ready_nodes(plan: PlanRecord) -> list[PlanNodeRecord]:
        return [candidate.node for candidate in PlanScheduler.ready_candidates(plan)]

    @staticmethod
    def ready_candidates(plan: PlanRecord) -> list[ReadyNodeCandidate]:
        nodes = {node.id: node for node in plan.nodes}
        dependencies: dict[str, set[str]] = {node.id: set() for node in plan.nodes}
        for edge in plan.edges:
            dependencies.setdefault(edge.successor_id, set()).add(edge.predecessor_id)
        ranks: dict[str, int] = {}
        unresolved = set(nodes)
        while unresolved:
            progressed = False
            for node_id in sorted(unresolved):
                parents = dependencies[node_id]
                if parents <= ranks.keys():
                    ranks[node_id] = 1 + max((ranks[parent] for parent in parents), default=0)
                    unresolved.remove(node_id)
                    progressed = True
                    break
            if not progressed:
                raise PlanValidationError("Plan contains a dependency cycle")
        return [
            ReadyNodeCandidate(node=node, dependency_rank=ranks[node.id])
            for node in sorted(
                plan.nodes,
                key=lambda item: (ranks[item.id], item.index, item.id),
            )
            if node.status == PlanNodeStatus.pending.value
            and all(
                nodes[dependency].status == PlanNodeStatus.completed.value
                for dependency in dependencies[node.id]
            )
        ]

    @staticmethod
    def dependency_broken_nodes(plan: PlanRecord) -> list[PlanNodeRecord]:
        broken = {
            node.id
            for node in plan.nodes
            if node.status
            in {
                PlanNodeStatus.failed.value,
                PlanNodeStatus.blocked.value,
            }
        }
        # Close over descendants so a failed root blocks the whole necessary
        # branch in one scheduler pass, including joins several levels away.
        changed = True
        while changed:
            changed = False
            for edge in plan.edges:
                if edge.predecessor_id in broken and edge.successor_id not in broken:
                    broken.add(edge.successor_id)
                    changed = True
        return [node for node in plan.nodes if node.id in broken]

    async def claim_ready_batch(
        self,
        run_id: str,
        *,
        requested_max_parallel_nodes: int | None = None,
    ) -> DispatchBatch | None:
        plan = await self.repository.active_for_run(run_id)
        if plan is None:
            return None
        for node in self.dependency_broken_nodes(plan):
            if node.status == PlanNodeStatus.pending.value:
                await self.repository.transition_node(
                    node.id,
                    PlanNodeStatus.blocked,
                    failure={"category": "dependency_broken"},
                )
        candidates = await self._filter_join_consumers(self.ready_candidates(plan))
        if not candidates:
            return None
        capacity = await self._claim_capacity(run_id, requested_max_parallel_nodes)
        if capacity is None:
            return None
        run, total_slots, occupied_indices, reservation_amounts, available = capacity
        occupied = len(occupied_indices)

        batch_id = str(uuid.uuid4())
        executions: list[NodeExecutionRecord] = []
        now = utc_now()
        free_slots = [index for index in range(total_slots) if index not in occupied_indices]
        eligible_candidates = await self._capability_eligible_candidates(
            run_id,
            candidates,
            available,
        )
        for candidate, slot_index in zip(
            eligible_candidates,
            free_slots[: len(eligible_candidates)],
            strict=True,
        ):
            claimed = await self.repository.session.execute(
                update(PlanNodeRecord)
                .where(
                    PlanNodeRecord.id == candidate.node.id,
                    PlanNodeRecord.status == PlanNodeStatus.pending.value,
                )
                .values(status=PlanNodeStatus.running.value, started_at=now)
            )
            if claimed.rowcount != 1:
                continue
            execution = await self.execution_repository.create_claim(
                run_id=run_id,
                plan_id=plan.id,
                plan_version=plan.version,
                plan_node_id=candidate.node.id,
                dispatch_batch_id=batch_id,
                worker_id=f"dispatch:{batch_id}",
                slot_index=slot_index,
            )
            await self.execution_repository.reserve_budgets(
                run_id=run_id,
                execution_id=execution.id,
                reservations=reservation_amounts,
            )
            executions.append(execution)
        if not executions:
            return None

        return await self._finalize_batch(
            run_id,
            plan,
            run,
            executions,
            eligible_candidates,
            batch_id,
            occupied,
            total_slots,
        )

    async def _claim_capacity(self, run_id, requested_max_parallel_nodes):
        run = await self.repository.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        configured = self._run_parallel_limit(run.reasoning_policy or {})
        requested = requested_max_parallel_nodes or configured
        total_slots = min(self.server_max_parallel_nodes, configured, max(1, requested))
        if not self.parallel_execution_enabled:
            total_slots = 1
        occupied_result = await self.repository.session.execute(
            select(NodeExecutionRecord.slot_index).where(
                NodeExecutionRecord.run_id == run_id,
                NodeExecutionRecord.slot_index.is_not(None),
                NodeExecutionRecord.status == "active",
            )
        )
        occupied_indices = {
            int(item) for item in occupied_result.scalars().all() if item is not None
        }
        reservation_amounts = self._reservation_amounts(run, total_slots)
        available = min(
            max(0, total_slots - len(occupied_indices)),
            await self._budget_available(run, reservation_amounts),
        )
        if available == 0:
            return None
        expected_version = run.state_version
        claimed = await self.repository.session.execute(
            update(RunRecord)
            .where(
                RunRecord.id == run_id,
                RunRecord.state_version == expected_version,
            )
            .values(state_version=expected_version + 1, updated_at=utc_now())
            .execution_options(synchronize_session=False)
        )
        if claimed.rowcount != 1:
            return None
        run.state_version = expected_version + 1
        return run, total_slots, occupied_indices, reservation_amounts, available

    async def _finalize_batch(
        self,
        run_id,
        plan,
        run,
        executions,
        eligible_candidates,
        batch_id,
        occupied,
        total_slots,
    ) -> DispatchBatch:
        state = dict(run.agent_state or {})
        state["active_plan_id"] = plan.id
        state["active_plan_version"] = plan.version
        existing = [
            item
            for item in state.get("active_executions", [])
            if item.get("plan_node_id") not in {execution.plan_node_id for execution in executions}
        ]
        summaries = [
            ActiveExecutionSummary(
                execution_id=execution.id,
                plan_node_id=execution.plan_node_id,
                plan_version=execution.plan_version,
                attempt=execution.attempt,
                dispatch_batch_id=execution.dispatch_batch_id,
                slot_index=execution.slot_index,
                phase=execution.phase,
                status=execution.status,
                started_at=execution.started_at,
                heartbeat_at=execution.heartbeat_at,
            ).model_dump(mode="json")
            for execution in executions
        ]
        state["active_executions"] = [*existing, *summaries]
        state["schema_version"] = 2
        state["version"] = max(
            int(state.get("version", 0)) + 1,
            int(run.state_version or 0),
        )
        run.agent_state = state
        run.state_version = state["version"]
        run.current_step_id = executions[0].plan_node_id if len(executions) == 1 else None
        selected_nodes = {candidate.node.id: candidate.node for candidate in eligible_candidates}
        for execution in executions:
            selected = selected_nodes[execution.plan_node_id]
            await self.repository._event(
                run_id,
                "plan.node.selected",
                {
                    "plan_id": plan.id,
                    "plan_version": plan.version,
                    "plan_node_id": selected.id,
                    "node_key": selected.node_key,
                    "node_execution_id": execution.id,
                    "dispatch_batch_id": batch_id,
                    "state_version": run.state_version,
                },
            )
        await self.repository._event(
            run_id,
            "plan.nodes.claimed",
            {
                "dispatch_batch_id": batch_id,
                "plan_id": plan.id,
                "plan_version": plan.version,
                "node_execution_ids": [item.id for item in executions],
                "plan_node_ids": [item.plan_node_id for item in executions],
                "used_slots": occupied + len(executions),
                "total_slots": total_slots,
                "state_version": run.state_version,
            },
        )
        await self.repository._event(
            run_id,
            "plan.parallelism.changed",
            {
                "plan_id": plan.id,
                "plan_version": plan.version,
                "dispatch_batch_id": batch_id,
                "used_slots": occupied + len(executions),
                "total_slots": total_slots,
                "active_count": occupied + len(executions),
            },
        )
        await self.repository.session.flush()
        return DispatchBatch(
            id=batch_id,
            plan_id=plan.id,
            plan_version=plan.version,
            executions=tuple(executions),
            total_slots=total_slots,
            used_slots=occupied + len(executions),
        )

    async def _filter_join_consumers(
        self, candidates: list[ReadyNodeCandidate]
    ) -> list[ReadyNodeCandidate]:
        if not candidates:
            return candidates
        candidate_ids = [item.node.id for item in candidates]
        joins = list(
            (
                await self.repository.session.scalars(
                    select(AgentJoinRecord).where(
                        AgentJoinRecord.consumer_plan_node_id.in_(candidate_ids),
                        AgentJoinRecord.status != "consumed",
                    )
                )
            ).all()
        )
        by_consumer: dict[str, list[AgentJoinRecord]] = {}
        for join in joins:
            if join.consumer_plan_node_id:
                by_consumer.setdefault(join.consumer_plan_node_id, []).append(join)
        ready: list[ReadyNodeCandidate] = []
        for candidate in candidates:
            pending = by_consumer.get(candidate.node.id, [])
            if not pending:
                ready.append(candidate)
            elif any(join.status == "blocked" for join in pending):
                await self.repository.transition_node(
                    candidate.node.id,
                    PlanNodeStatus.blocked,
                    failure={
                        "category": "subagent_join_blocked",
                        "join_ids": [join.id for join in pending if join.status == "blocked"],
                    },
                )
        return ready

    async def select_next(self, run_id: str) -> PlanNodeRecord | None:
        batch = await self.claim_ready_batch(run_id, requested_max_parallel_nodes=1)
        if batch is None:
            return None
        execution = batch.executions[0]
        execution = await self.execution_repository.transition(
            execution.id,
            expected_version=execution.state_version,
            phase=NodeExecutionPhase.running,
        )
        selected = await self.repository.require_node(execution.plan_node_id)
        run = await self.repository.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        state = dict(run.agent_state or {})
        state["active_executions"] = [
            {
                **item,
                "phase": NodeExecutionPhase.running.value,
                "state_version": execution.state_version,
                "heartbeat_at": execution.heartbeat_at.isoformat(),
            }
            if item.get("execution_id") == execution.id
            else item
            for item in state.get("active_executions", [])
        ]
        run.agent_state = state
        run.current_step_id = selected.id
        await self.repository._event(
            run_id,
            "plan.node.execution_started",
            {
                "node_execution_id": execution.id,
                "plan_id": batch.plan_id,
                "plan_version": execution.plan_version,
                "plan_node_id": selected.id,
                "attempt": execution.attempt,
                "dispatch_batch_id": execution.dispatch_batch_id,
                "slot_index": execution.slot_index,
                "phase": execution.phase,
                "status": execution.status,
                "state_version": execution.state_version,
                "started_at": execution.started_at.isoformat(),
                "heartbeat_at": execution.heartbeat_at.isoformat(),
            },
        )
        await self.repository.session.flush()
        return selected

    async def clear_active_node(self, run_id: str, node_id: str) -> None:
        from app.db.models.runs import RunRecord

        run = await self.repository.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        state = dict(run.agent_state or {})
        previous = list(state.get("active_executions", []))
        active = [item for item in previous if item.get("plan_node_id") != node_id]
        if len(active) != len(previous):
            state["active_executions"] = active
            state["version"] = int(state.get("version", run.state_version or 0)) + 1
            run.agent_state = state
            run.state_version = state["version"]
            run.current_step_id = None
            await self.repository.session.flush()

    @staticmethod
    def _run_parallel_limit(reasoning_policy: dict[str, Any]) -> int:
        effective = reasoning_policy.get("effective") or {}
        budgets = effective.get("budgets") or {}
        return max(1, int(budgets.get("max_parallel_nodes", 3)))

    async def _budget_available(
        self,
        run: RunRecord,
        reservation_amounts: dict[str, int],
    ) -> int:
        effective = (run.reasoning_policy or {}).get("effective") or {}
        budgets = effective.get("budgets") or {}
        limits = {
            "turns": budgets.get("max_turns"),
            "tool_calls": budgets.get("max_tool_calls"),
            "model_calls": budgets.get("max_model_calls"),
        }
        usage = (run.agent_state or {}).get("budget_usage") or {}
        result = await self.repository.session.execute(
            select(
                BudgetReservationRecord.budget_kind,
                func.sum(BudgetReservationRecord.reserved),
            )
            .where(
                BudgetReservationRecord.run_id == run.id,
                BudgetReservationRecord.status == "reserved",
            )
            .group_by(BudgetReservationRecord.budget_kind)
        )
        reserved = {kind: int(amount or 0) for kind, amount in result.all()}
        available = self.server_max_parallel_nodes
        for kind, limit in limits.items():
            if limit is None:
                continue
            available = min(
                available,
                max(0, int(limit) - int(usage.get(kind, 0)) - reserved.get(kind, 0))
                // max(1, reservation_amounts.get(kind, 1)),
            )
        return available

    @staticmethod
    def _reservation_amounts(
        run: RunRecord,
        total_slots: int,
    ) -> dict[str, int]:
        budgets = ((run.reasoning_policy or {}).get("effective") or {}).get("budgets") or {}
        defaults = {"turns": 6, "tool_calls": 3, "model_calls": 6}
        limits = {
            "turns": budgets.get("max_turns"),
            "tool_calls": budgets.get("max_tool_calls"),
            "model_calls": budgets.get("max_model_calls"),
        }
        return {
            kind: max(
                1,
                min(
                    defaults[kind],
                    int(limit) // max(1, total_slots) if limit is not None else defaults[kind],
                ),
            )
            for kind, limit in limits.items()
        }

    async def _capability_eligible_candidates(
        self,
        run_id: str,
        candidates: list[ReadyNodeCandidate],
        available: int,
    ) -> list[ReadyNodeCandidate]:
        result = await self.repository.session.execute(
            select(PlanNodeRecord.required_capabilities)
            .join(
                NodeExecutionRecord,
                NodeExecutionRecord.plan_node_id == PlanNodeRecord.id,
            )
            .where(
                NodeExecutionRecord.run_id == run_id,
                NodeExecutionRecord.status == "active",
                NodeExecutionRecord.slot_index.is_not(None),
            )
        )
        counts: dict[str, int] = {}
        for capabilities in result.scalars().all():
            for capability in capabilities or []:
                counts[capability] = counts.get(capability, 0) + 1
        eligible: list[ReadyNodeCandidate] = []
        for candidate in candidates:
            capabilities = list(candidate.node.required_capabilities or [])
            if (
                self.parallel_safe_capabilities is not None
                and not set(capabilities) <= self.parallel_safe_capabilities
            ):
                continue
            blocked = any(
                counts.get(capability, 0)
                >= (
                    self.provider_concurrency_limit
                    if capability.startswith("provider:")
                    else self.capability_concurrency_limit
                )
                for capability in capabilities
            )
            if blocked:
                continue
            eligible.append(candidate)
            for capability in capabilities:
                counts[capability] = counts.get(capability, 0) + 1
            if len(eligible) >= available:
                break
        return eligible
