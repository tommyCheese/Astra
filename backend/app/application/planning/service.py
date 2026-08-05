"""Plan creation, validation, and mutation services."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.common.schemas.agent.execution_state import AgentState, Evaluation
from app.common.schemas.agent.planning import (
    PlanDraft,
    PlanNodeDraft,
    PlanPatch,
    TaskContract,
)
from app.common.schemas.agent.run_policy import RunBudgets
from app.common.schemas.agent.types import (
    EvaluationOutcome,
    NodeExecutionPhase,
    NodeExecutionStatus,
    PlanNodeStatus,
    PlanStatus,
)
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.executions import NodeExecutionRecord
from app.infrastructure.db.models.plans import PlanNodeRecord, PlanRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.repositories.executions import NodeExecutionRepository
from app.infrastructure.repositories.plans import PlanRepository, PlanStateError, plan_to_view


class PlanValidationError(ValueError):
    """Raised when a Plan violates graph or runtime constraints."""

__all__ = [
    "PlanService",
    "PlanValidator",
    "canonical_agent_state",
]


def _require_active_plan(plan):
    if plan is None:
        raise PlanStateError("Run has no active plan")
    return plan


class PlanValidator:
    def validate(
        self,
        draft: PlanDraft,
        *,
        task_contract: TaskContract,
        available_capabilities: set[str] | None = None,
        forbidden_capabilities: set[str] | None = None,
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
        forbidden_capabilities = forbidden_capabilities or set()
        for node in draft.nodes:
            self._validate_node(
                node,
                known,
                criteria,
                contract_skills,
                available_capabilities,
                forbidden_capabilities,
            )
        depth = self._validate_acyclic(draft)
        self._validate_budgets(draft, depth, budgets)
        return draft

    @staticmethod
    def _validate_node(
        node, known, criteria, contract_skills, available_capabilities, forbidden_capabilities
    ) -> None:
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
        PlanValidator._validate_bindings(node, available_capabilities, forbidden_capabilities)
        unknown_skills = set(node.required_skill_ids) - contract_skills
        if unknown_skills:
            raise PlanValidationError(
                f"Unbound Skills for {node.node_key}: {sorted(unknown_skills)}"
            )

    @staticmethod
    def _validate_bindings(node, available_capabilities, forbidden_capabilities) -> None:
        concrete_prefixes = ("provider:", "permission:", "backend:", "executor:", "tool:")
        forbidden = set(node.required_capabilities) & forbidden_capabilities
        concrete = {
            item for item in node.required_capabilities if item.startswith(concrete_prefixes)
        }
        if forbidden or concrete:
            raise PlanValidationError(
                f"Concrete runtime bindings are not allowed for {node.node_key}: "
                f"{sorted(forbidden | concrete)}"
            )
        unknown = (
            set(node.required_capabilities) - available_capabilities
            if available_capabilities is not None
            else set()
        )
        if unknown:
            raise PlanValidationError(
                f"Unavailable capabilities for {node.node_key}: {sorted(unknown)}"
            )

    @staticmethod
    def _validate_budgets(draft, depth, budgets) -> None:
        if not any(not node.depends_on for node in draft.nodes):
            raise PlanValidationError("Plan requires at least one root node")
        if budgets and len(draft.nodes) > max(1, budgets.max_plan_depth * 4):
            raise PlanValidationError("Plan node budget exceeded")
        if budgets and depth > budgets.max_plan_depth:
            raise PlanValidationError("Plan depth budget exceeded")

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
        forbidden_capabilities: set[str] | None = None,
        budgets: RunBudgets | None = None,
        activate: bool = True,
    ) -> PlanRecord:
        validated = self.validator.validate(
            draft,
            task_contract=contract,
            available_capabilities=capabilities,
            forbidden_capabilities=forbidden_capabilities,
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
        forbidden_capabilities: set[str] | None = None,
        budgets: RunBudgets | None = None,
    ) -> PlanRecord:
        current = _require_active_plan(await self.repository.active_for_run(run_id))
        if current.version != patch.expected_plan_version:
            error = PlanStateError(
                f"Plan version conflict: expected {patch.expected_plan_version}, "
                f"got {current.version}"
            )
            await self._record_patch_rejection(run_id, patch, error)
            raise error
        view = plan_to_view(current)
        running_node_ids = {
            node.id for node in view.nodes if node.status.value == PlanNodeStatus.running.value
        }
        if running_node_ids:
            active = await NodeExecutionRepository(self.repository.session).active_for_run(run_id)
            owned_running_node_ids = {
                execution.plan_node_id for execution in active if execution.plan_id == current.id
            }
            if not running_node_ids <= owned_running_node_ids:
                error = PlanStateError("Cannot replan while an unowned plan node is running")
                await self._record_patch_rejection(run_id, patch, error)
                raise error
            await self._drain_for_replan(run_id, current)
            view = plan_to_view(current)
        nodes = self._editable_nodes(view)
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
                forbidden_capabilities=forbidden_capabilities,
                budgets=budgets,
            )
        except (TypeError, ValueError) as exc:
            await self._record_patch_rejection(run_id, patch, exc)
            raise
        lineage, node_state = self._lineage_state(current, nodes)
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

    @staticmethod
    def _editable_nodes(view) -> dict[str, dict[str, Any]]:
        return {
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

    @staticmethod
    def _lineage_state(current, nodes):
        lineage = {key: value["id"] for key, value in nodes.items() if value.get("id")}
        original = {node.id: node for node in current.nodes}
        state = {}
        for key, value in nodes.items():
            previous = original.get(value.get("id"))
            state[key] = {
                "status": value.get("status", PlanNodeStatus.pending.value),
                "evidence_refs": list(previous.evidence_refs or []) if previous else [],
                "failure": previous.failure if previous else None,
                "started_at": previous.started_at if previous else None,
                "completed_at": previous.completed_at if previous else None,
            }
        return lineage, state

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
        if kind == "add_node":
            PlanService._add_node(nodes, operation)
            return
        node_key = operation.get("node_key")
        if not node_key or node_key not in nodes:
            raise PlanValidationError(f"Unknown plan node: {node_key}")
        node = nodes[node_key]
        if node.get("status") in {PlanNodeStatus.completed.value, PlanNodeStatus.running.value}:
            raise PlanStateError(f"Cannot modify {node['status']} plan node: {node_key}")
        handler = {
            "update_node": PlanService._update_node,
            "add_dependency": PlanService._add_dependency,
            "remove_dependency": PlanService._remove_dependency,
            "skip_node": PlanService._skip_node,
            "block_node": PlanService._block_node,
        }.get(kind)
        if handler is None:
            raise PlanValidationError(f"Unsupported plan patch operation: {kind}")
        handler(nodes, node, node_key, operation)

    @staticmethod
    def _add_node(nodes, operation) -> None:
        raw = operation.get("node")
        if not raw:
            raise PlanValidationError("add_node requires node")
        node = PlanNodeDraft.model_validate(raw).model_dump(mode="json")
        if node["node_key"] in nodes:
            raise PlanValidationError(f"Duplicate plan node: {node['node_key']}")
        nodes[node["node_key"]] = node

    @staticmethod
    def _update_node(_nodes, node, _node_key, operation) -> None:
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
            {key: value for key, value in operation.get("updates", {}).items() if key in allowed}
        )

    @staticmethod
    def _add_dependency(nodes, node, _node_key, operation) -> None:
        predecessor = operation.get("predecessor_key")
        if predecessor not in nodes:
            raise PlanValidationError(f"Unknown predecessor: {predecessor}")
        node["depends_on"] = list(dict.fromkeys([*node["depends_on"], predecessor]))

    @staticmethod
    def _remove_dependency(_nodes, node, _node_key, operation) -> None:
        predecessor = operation.get("predecessor_key")
        node["depends_on"] = [item for item in node["depends_on"] if item != predecessor]

    @staticmethod
    def _skip_node(_nodes, node, node_key, _operation) -> None:
        if not node.get("optional"):
            raise PlanStateError(f"Required node cannot be skipped: {node_key}")
        node["status"] = PlanNodeStatus.skipped.value

    @staticmethod
    def _block_node(_nodes, node, _node_key, _operation) -> None:
        node["status"] = PlanNodeStatus.blocked.value


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
