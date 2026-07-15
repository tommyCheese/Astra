from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.db.models import PlanNodeRecord, PlanRecord
from app.repositories.plans import PlanRepository, PlanStateError, plan_to_view
from app.schemas.agent import (
    AgentState,
    ExpectedObservation,
    PlanDraft,
    PlanNodeDraft,
    PlanNodeStatus,
    PlanOutput,
    PlanPatch,
    PlanStatus,
    PlanningStrategy,
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
                key for key, values in dependencies.items() if key not in resolved and values <= resolved
            )
            if not ready:
                raise PlanValidationError("Plan contains a dependency cycle")
            for key in ready:
                depth[key] = 1 + max((depth[item] for item in dependencies[key]), default=0)
                resolved.add(key)
        return max(depth.values(), default=0)


def plan_output_to_draft(
    plan: PlanOutput,
    *,
    strategy: PlanningStrategy,
    contract: TaskContract,
) -> PlanDraft:
    criterion_ids = [item.id for item in contract.success_criteria]
    nodes: list[PlanNodeDraft] = []
    for index, item in enumerate(plan.steps, start=1):
        node_key = f"step-{index}"
        nodes.append(
            PlanNodeDraft(
                node_key=node_key,
                title=item.title,
                intent=item.intent,
                depends_on=[]
                if strategy == PlanningStrategy.direct or index == 1
                else [f"step-{index - 1}"],
                required_capabilities=list(item.required_tools),
                success_criteria_refs=criterion_ids,
                expected_outcome=ExpectedObservation(
                    kind="step_result",
                    success_condition="step completed with accepted evidence",
                ),
                risk_level=plan.risk_level,
            )
        )
    return PlanDraft(strategy=strategy, nodes=nodes)


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
            raise PlanStateError(
                f"Plan version conflict: expected {patch.expected_plan_version}, "
                f"got {current.version}"
            )
        view = plan_to_view(current)
        nodes: dict[str, dict[str, Any]] = {
            node.node_key: {
                "node_key": node.node_key,
                "title": node.title,
                "intent": node.intent,
                "depends_on": list(node.depends_on),
                "required_capabilities": list(node.required_capabilities),
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
        for operation in patch.operations:
            self._apply_operation(nodes, operation.model_dump(exclude_none=True))
        draft = PlanDraft(
            strategy=view.strategy,
            nodes=[
                PlanNodeDraft.model_validate(
                    {key: value for key, value in node.items() if key not in {"status", "id"}}
                )
                for node in sorted(nodes.values(), key=lambda item: item["node_key"])
                if node.get("status") != PlanNodeStatus.blocked.value
            ],
        )
        self.validator.validate(
            draft,
            task_contract=contract,
            available_capabilities=capabilities,
            budgets=budgets,
        )
        lineage = {key: value["id"] for key, value in nodes.items() if value.get("id")}
        next_plan = await self.repository.create(
            run_id,
            draft,
            status=PlanStatus.active,
            supersedes_plan_id=current.id,
            lineage=lineage,
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
        return next_plan

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
                "success_criteria_refs",
                "expected_outcome",
                "risk_level",
                "optional",
            }
            node.update({key: value for key, value in operation.get("updates", {}).items() if key in allowed})
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


class PlanScheduler:
    def __init__(self, repository: PlanRepository):
        self.repository = repository

    @staticmethod
    def ready_nodes(plan: PlanRecord) -> list[PlanNodeRecord]:
        nodes = {node.id: node for node in plan.nodes}
        dependencies: dict[str, set[str]] = {node.id: set() for node in plan.nodes}
        for edge in plan.edges:
            dependencies.setdefault(edge.successor_id, set()).add(edge.predecessor_id)
        return [
            node
            for node in sorted(plan.nodes, key=lambda item: item.index)
            if node.status == PlanNodeStatus.pending.value
            and all(
                nodes[dependency].status == PlanNodeStatus.completed.value
                for dependency in dependencies[node.id]
            )
        ]

    @staticmethod
    def dependency_broken_nodes(plan: PlanRecord) -> list[PlanNodeRecord]:
        nodes = {node.id: node for node in plan.nodes}
        broken: set[str] = set()
        for edge in plan.edges:
            predecessor = nodes[edge.predecessor_id]
            if predecessor.status in {
                PlanNodeStatus.failed.value,
                PlanNodeStatus.blocked.value,
            }:
                broken.add(edge.successor_id)
        return [node for node in plan.nodes if node.id in broken]

    async def select_next(self, run_id: str) -> PlanNodeRecord | None:
        plan = await self.repository.active_for_run(run_id)
        if plan is None:
            return None
        ready = self.ready_nodes(plan)
        if not ready:
            for node in self.dependency_broken_nodes(plan):
                if node.status == PlanNodeStatus.pending.value:
                    await self.repository.transition_node(
                        node.id,
                        PlanNodeStatus.blocked,
                        failure={"category": "dependency_broken"},
                    )
            return None
        selected = await self.repository.transition_node(ready[0].id, PlanNodeStatus.running)
        from app.db.models import RunRecord

        run = await self.repository.session.get(RunRecord, run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        state = dict(run.agent_state or {})
        state["active_plan_id"] = plan.id
        state["active_plan_version"] = plan.version
        state["active_node_id"] = selected.id
        state["version"] = int(state.get("version", run.state_version or 0)) + 1
        run.agent_state = state
        run.state_version = state["version"]
        run.current_step_id = selected.id
        await self.repository._event(
            run_id,
            "plan.node.selected",
            {
                "plan_id": plan.id,
                "plan_version": plan.version,
                "plan_node_id": selected.id,
                "node_key": selected.node_key,
                "state_version": run.state_version,
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
        if state.get("active_node_id") == node_id:
            state["active_node_id"] = None
            state["version"] = int(state.get("version", run.state_version or 0)) + 1
            run.agent_state = state
            run.state_version = state["version"]
            run.current_step_id = None
            await self.repository.session.flush()


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
