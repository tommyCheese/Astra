from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, update

from app.db.models import (
    BudgetReservationRecord,
    NodeExecutionRecord,
    PlanNodeRecord,
    PlanRecord,
    RunRecord,
    utc_now,
)
from app.repositories.executions import NodeExecutionRepository
from app.repositories.plans import PlanRepository, PlanStateError, plan_to_view
from app.schemas.agent import (
    ActiveExecutionSummary,
    AgentState,
    Evaluation,
    EvaluationOutcome,
    NodeExecutionPhase,
    NodeExecutionStatus,
    PlanDraft,
    PlanNodeDraft,
    PlanNodeStatus,
    PlanPatch,
    PlanStatus,
    RunBudgets,
    TaskContract,
)


class PlanValidationError(ValueError):
    pass


class PlanValidator:
    def validate(
        self,
        draft: PlanDraft,
        *,
        task_contract: TaskContract,
        available_capabilities: set[str] | None = None,
        budgets: RunBudgets | None = None,
    ) -> PlanDraft:
        keys = [node.node_key for node in draft.nodes]
        if len(keys) != len(set(keys)):
            raise PlanValidationError("Plan node keys must be unique")
        known = set(keys)
        criteria = {item.id for item in task_contract.success_criteria}
        contract_skills = {
            item.get("qualified_identity")
            for item in task_contract.skill_revisions
            if item.get("qualified_identity")
        }
        available_capabilities = available_capabilities or set()
        for node in draft.nodes:
            unknown_dependencies = set(node.depends_on) - known
            if unknown_dependencies:
                raise PlanValidationError(
                    f"Unknown dependencies for {node.node_key}: {sorted(unknown_dependencies)}"
                )
            if node.node_key in node.depends_on:
                raise PlanValidationError(f"Plan node {node.node_key} depends on itself")
            unknown_criteria = set(node.success_criteria_refs) - criteria
            if unknown_criteria:
                raise PlanValidationError(
                    f"Unknown success criteria for {node.node_key}: {sorted(unknown_criteria)}"
                )
            if available_capabilities:
                unknown_capabilities = set(node.required_capabilities) - available_capabilities
                if unknown_capabilities:
                    raise PlanValidationError(
                        f"Unavailable capabilities for {node.node_key}: "
                        f"{sorted(unknown_capabilities)}"
                    )
            unknown_skills = set(node.required_skill_ids) - contract_skills
            if unknown_skills:
                raise PlanValidationError(
                    f"Unbound Skills for {node.node_key}: {sorted(unknown_skills)}"
                )
        depth = self._validate_acyclic(draft)
        roots = [node.node_key for node in draft.nodes if not node.depends_on]
        if not roots:
            raise PlanValidationError("Plan requires at least one root node")
        if budgets and len(draft.nodes) > max(1, budgets.max_plan_depth * 4):
            raise PlanValidationError("Plan node budget exceeded")
        if budgets and depth > budgets.max_plan_depth:
            raise PlanValidationError("Plan depth budget exceeded")
        return draft

    @staticmethod
    def _validate_acyclic(draft: PlanDraft) -> int:
        dependencies = {node.node_key: set(node.depends_on) for node in draft.nodes}
        resolved: set[str] = set()
        depth: dict[str, int] = {}
        while len(resolved) < len(dependencies):
            ready = sorted(
                key
                for key, values in dependencies.items()
                if key not in resolved and values <= resolved
            )
            if not ready:
                raise PlanValidationError("Plan contains a dependency cycle")
            for key in ready:
                depth[key] = 1 + max((depth[item] for item in dependencies[key]), default=0)
                resolved.add(key)
        return max(depth.values(), default=0)


class PlanService:
    def __init__(self, repository: PlanRepository):
        self.repository = repository
        self.validator = PlanValidator()

    async def create(
        self,
        run_id: str,
        draft: PlanDraft,
        *,
        contract: TaskContract,
        capabilities: set[str] | None = None,
        budgets: RunBudgets | None = None,
        activate: bool = True,
    ) -> PlanRecord:
        validated = self.validator.validate(
            draft,
            task_contract=contract,
            available_capabilities=capabilities,
            budgets=budgets,
        )
        return await self.repository.create(
            run_id,
            validated,
            status=PlanStatus.active if activate else PlanStatus.planned,
        )

    async def apply_patch(
        self,
        run_id: str,
        patch: PlanPatch,
        *,
        contract: TaskContract,
        capabilities: set[str] | None = None,
        budgets: RunBudgets | None = None,
    ) -> PlanRecord:
        current = await self.repository.active_for_run(run_id)
        if current is None:
            raise PlanStateError("Run has no active plan")
        if current.version != patch.expected_plan_version:
            error = PlanStateError(
                f"Plan version conflict: expected {patch.expected_plan_version}, "
                f"got {current.version}"
            )
            await self._record_patch_rejection(run_id, patch, error)
            raise error
        view = plan_to_view(current)
        running_node_ids = {
            node.id
            for node in view.nodes
            if node.status.value == PlanNodeStatus.running.value
        }
        if running_node_ids:
            active = await NodeExecutionRepository(
                self.repository.session
            ).active_for_run(run_id)
            owned_running_node_ids = {
                execution.plan_node_id
                for execution in active
                if execution.plan_id == current.id
            }
            if not running_node_ids <= owned_running_node_ids:
                error = PlanStateError(
                    "Cannot replan while an unowned plan node is running"
                )
                await self._record_patch_rejection(run_id, patch, error)
                raise error
            await self._drain_for_replan(run_id, current)
            view = plan_to_view(current)
        nodes: dict[str, dict[str, Any]] = {
            node.node_key: {
                "node_key": node.node_key,
                "title": node.title,
                "intent": node.intent,
                "depends_on": list(node.depends_on),
                "required_capabilities": list(node.required_capabilities),
                "required_skill_ids": list(node.required_skill_ids),
                "success_criteria_refs": list(node.success_criteria_refs),
                "expected_outcome": node.expected_outcome.model_dump(mode="json")
                if node.expected_outcome
                else {
                    "kind": "step_result",
                    "success_condition": "step completed with accepted evidence",
                },
                "risk_level": node.risk_level,
                "optional": node.optional,
                "status": node.status.value,
                "id": node.id,
            }
            for node in view.nodes
        }
        try:
            for operation in patch.operations:
                self._apply_operation(nodes, operation.model_dump(exclude_none=True))
            draft = PlanDraft(
                nodes=[
                    PlanNodeDraft.model_validate(
                        {key: value for key, value in node.items() if key not in {"status", "id"}}
                    )
                    for node in sorted(nodes.values(), key=lambda item: item["node_key"])
                ],
            )
            self.validator.validate(
                draft,
                task_contract=contract,
                available_capabilities=capabilities,
                budgets=budgets,
            )
        except (TypeError, ValueError) as exc:
            await self._record_patch_rejection(run_id, patch, exc)
            raise
        lineage = {key: value["id"] for key, value in nodes.items() if value.get("id")}
        original_nodes = {node.id: node for node in current.nodes}
        node_state = {
            key: {
                "status": value.get("status", PlanNodeStatus.pending.value),
                "evidence_refs": list(original_nodes[value["id"]].evidence_refs or [])
                if value.get("id") in original_nodes
                else [],
                "failure": original_nodes[value["id"]].failure
                if value.get("id") in original_nodes
                else None,
                "started_at": original_nodes[value["id"]].started_at
                if value.get("id") in original_nodes
                else None,
                "completed_at": original_nodes[value["id"]].completed_at
                if value.get("id") in original_nodes
                else None,
            }
            for key, value in nodes.items()
        }
        next_plan = await self.repository.create(
            run_id,
            draft,
            status=PlanStatus.active,
            supersedes_plan_id=current.id,
            lineage=lineage,
            node_state=node_state,
        )
        await self.repository._event(
            run_id,
            "plan.patch_applied",
            {
                "previous_plan_id": current.id,
                "plan_id": next_plan.id,
                "previous_version": current.version,
                "plan_version": next_plan.version,
                "reason": patch.reason,
            },
        )
        run = await self.repository.session.get(RunRecord, run_id)
        if run is not None and run.status == "draining_for_replan":
            run.status = "executing"
            run.updated_at = utc_now()
        return next_plan

    async def _drain_for_replan(
        self,
        run_id: str,
        plan: PlanRecord,
    ) -> None:
        run = await self.repository.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        run.status = "draining_for_replan"
        run.updated_at = utc_now()
        await self.repository._event(
            run_id,
            "plan.revision.draining",
            {
                "plan_id": plan.id,
                "plan_version": plan.version,
            },
        )
        execution_repository = NodeExecutionRepository(self.repository.session)
        active = await execution_repository.active_for_run(run_id)
        for execution in active:
            if execution.plan_id != plan.id:
                continue
            await execution_repository.release_leases(
                execution.id,
                reason="replan",
            )
            await execution_repository.settle_budgets(
                execution.id,
                consumed={},
                status="cancelled",
            )
            await execution_repository.transition(
                execution.id,
                expected_version=execution.state_version,
                phase=NodeExecutionPhase.cancelled,
                status=NodeExecutionStatus.cancelled,
                failure={"category": "replanned"},
            )
            node = await self.repository.require_node(execution.plan_node_id)
            if node.status == PlanNodeStatus.running.value:
                node.status = PlanNodeStatus.pending.value
                node.started_at = None
                node.completed_at = None
        state = dict(run.agent_state or {})
        state["active_executions"] = []
        state["version"] = max(
            int(state.get("version", 0)) + 1,
            run.state_version + 1,
        )
        run.agent_state = state
        run.state_version = state["version"]
        run.current_step_id = None
        await self.repository.session.flush()

    async def _record_patch_rejection(
        self, run_id: str, patch: PlanPatch, error: Exception
    ) -> None:
        await self.repository._event(
            run_id,
            "plan.patch_rejected",
            {
                "expected_plan_version": patch.expected_plan_version,
                "reason": patch.reason,
                "error": str(error),
            },
        )
        await self.repository.session.flush()

    async def complete_node(
        self,
        run_id: str,
        node_id: str,
        *,
        evaluation: Evaluation | None,
        evidence_refs: list[str],
    ) -> PlanNodeRecord:
        if evaluation is not None and evaluation.outcome != EvaluationOutcome.matched:
            raise PlanStateError("Plan node completion requires a matched evaluation")
        node = await self.repository.require_node(node_id)
        plan = await self.repository.require(node.plan_id)
        if plan.run_id != run_id:
            raise PlanStateError("Plan node does not belong to the Run")
        completed = await self.repository.transition_node(
            node_id,
            PlanNodeStatus.completed,
            evidence_refs=evidence_refs,
        )
        run = await self.repository.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        state = AgentState.model_validate(run.agent_state or {})
        if evaluation is not None:
            state.evaluations.append(evaluation.model_dump(mode="json"))
            for criterion in state.task_contract.success_criteria:
                if criterion.id in evaluation.criterion_updates:
                    criterion.status = evaluation.criterion_updates[criterion.id]
        result = await self.repository.session.execute(
            select(NodeExecutionRecord).where(
                NodeExecutionRecord.plan_node_id == node_id,
                NodeExecutionRecord.current_slot == "current",
            )
        )
        execution = result.scalar_one_or_none()
        if execution is not None:
            execution_repository = NodeExecutionRepository(self.repository.session)
            await execution_repository.settle_budgets(execution.id, consumed={})
            await execution_repository.release_leases(execution.id, reason="completed")
            await execution_repository.transition(
                execution.id,
                expected_version=execution.state_version,
                phase=NodeExecutionPhase.completed,
                status=NodeExecutionStatus.completed,
                result={"evidence_refs": list(evidence_refs)},
            )
        state.active_executions = [
            item for item in state.active_executions if item.plan_node_id != node_id
        ]
        state.active_plan_id = plan.id
        state.active_plan_version = plan.version
        state.version = run.state_version + 1
        run.agent_state = state.model_dump(mode="json")
        run.state_version = state.version
        run.current_step_id = None
        await self.repository.session.flush()
        return completed

    @staticmethod
    def _apply_operation(nodes: dict[str, dict[str, Any]], operation: dict[str, Any]) -> None:
        kind = operation["operation"]
        node_key = operation.get("node_key")
        if kind == "add_node":
            raw = operation.get("node")
            if not raw:
                raise PlanValidationError("add_node requires node")
            node = PlanNodeDraft.model_validate(raw).model_dump(mode="json")
            if node["node_key"] in nodes:
                raise PlanValidationError(f"Duplicate plan node: {node['node_key']}")
            nodes[node["node_key"]] = node
            return
        if not node_key or node_key not in nodes:
            raise PlanValidationError(f"Unknown plan node: {node_key}")
        node = nodes[node_key]
        if node.get("status") in {PlanNodeStatus.completed.value, PlanNodeStatus.running.value}:
            raise PlanStateError(f"Cannot modify {node['status']} plan node: {node_key}")
        if kind == "update_node":
            allowed = {
                "title",
                "intent",
                "required_capabilities",
                "required_skill_ids",
                "success_criteria_refs",
                "expected_outcome",
                "risk_level",
                "optional",
            }
            node.update(
                {
                    key: value
                    for key, value in operation.get("updates", {}).items()
                    if key in allowed
                }
            )
        elif kind == "add_dependency":
            predecessor = operation.get("predecessor_key")
            if predecessor not in nodes:
                raise PlanValidationError(f"Unknown predecessor: {predecessor}")
            node["depends_on"] = list(dict.fromkeys([*node["depends_on"], predecessor]))
        elif kind == "remove_dependency":
            predecessor = operation.get("predecessor_key")
            node["depends_on"] = [item for item in node["depends_on"] if item != predecessor]
        elif kind == "skip_node":
            if not node.get("optional"):
                raise PlanStateError(f"Required node cannot be skipped: {node_key}")
            node["status"] = PlanNodeStatus.skipped.value
        elif kind == "block_node":
            node["status"] = PlanNodeStatus.blocked.value
        else:
            raise PlanValidationError(f"Unsupported plan patch operation: {kind}")


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
        candidates = self.ready_candidates(plan)
        if not candidates:
            return None
        from app.db.models import RunRecord

        run = await self.repository.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        configured = self._run_parallel_limit(run.reasoning_policy or {})
        requested = requested_max_parallel_nodes or configured
        total_slots = min(
            self.server_max_parallel_nodes,
            configured,
            max(1, requested),
        )
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
        occupied = len(occupied_indices)
        available = max(0, total_slots - occupied)
        reservation_amounts = self._reservation_amounts(run, total_slots)
        budget_available = await self._budget_available(run, reservation_amounts)
        available = min(available, budget_available)
        if available == 0:
            return None

        # Claim scheduling ownership with the Run state version before touching
        # nodes or reservations. This is the transaction-level mutex that keeps
        # concurrent coordinators from both observing the same free slots.
        expected_run_version = run.state_version
        scheduler_claim = await self.repository.session.execute(
            update(RunRecord)
            .where(
                RunRecord.id == run_id,
                RunRecord.state_version == expected_run_version,
            )
            .values(state_version=expected_run_version + 1, updated_at=utc_now())
            .execution_options(synchronize_session=False)
        )
        if scheduler_claim.rowcount != 1:
            return None
        run.state_version = expected_run_version + 1

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
        state.pop("active_node_id", None)
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
        from app.db.models import RunRecord

        run = await self.repository.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        state = dict(run.agent_state or {})
        previous = list(state.get("active_executions", []))
        active = [item for item in previous if item.get("plan_node_id") != node_id]
        if state.get("active_node_id") == node_id or len(active) != len(previous):
            state.pop("active_node_id", None)
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
        budgets = (
            ((run.reasoning_policy or {}).get("effective") or {}).get("budgets")
            or {}
        )
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


def canonical_agent_state(
    contract: TaskContract,
    plan: PlanRecord,
    *,
    policy_version: int,
) -> AgentState:
    return AgentState(
        task_contract=contract,
        policy_version=policy_version,
        active_plan_id=plan.id,
        active_plan_version=plan.version,
    )
