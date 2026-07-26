from __future__ import annotations

import json

from app.agent_profile import AgentProfile, load_agent_profile
from app.core.config import Settings
from app.repositories.plans import PlanRepository
from app.repositories.runs import RunRepository
from app.runner.model_client import build_model_client
from app.runner.planning import PlanValidator
from app.schemas.agent import (
    PlanNodeStatus,
    PlanStatus,
    ReasoningPolicySnapshot,
    TaskContract,
)
from app.tools.registry import build_tool_registry


class PlanRevisionError(ValueError):
    def __init__(self, message: str, *, code: str = "PLAN_REVISION_INVALID"):
        super().__init__(message)
        self.code = code


async def revise_waiting_plan(
    repository: RunRepository,
    settings: Settings,
    *,
    run_id: str,
    request: str,
    continuation_token: str,
    plan_id: str,
    expected_plan_version: int,
    expected_state_version: int,
):
    run, current = await repository.claim_plan_revision(
        run_id,
        continuation_token=continuation_token,
        plan_id=plan_id,
        expected_plan_version=expected_plan_version,
        expected_state_version=expected_state_version,
    )
    current_plan_id = current.id
    current_plan_version = current.version
    try:
        contract = TaskContract.model_validate(run.task_contract)
        policy = ReasoningPolicySnapshot.model_validate(run.reasoning_policy)
        client = build_model_client(settings)
        profile = (
            AgentProfile.from_snapshot(run.agent_profile_snapshot)
            if run.agent_profile_snapshot
            and run.agent_profile_snapshot.get("version") != "legacy-unversioned"
            else load_agent_profile()
        )
        client.bind_agent_profile(profile)
        client.bind_reasoning_effort(policy.effective.reasoning_effort)
        revision_context = {
            "original_goal": contract.original_goal,
            "revision_request": request.strip(),
            "current_plan": [
                {
                    "node_key": node.node_key,
                    "title": node.title,
                    "intent": node.intent,
                    "depends_on": _dependency_keys(current, node.id),
                    "status": node.status,
                }
                for node in sorted(current.nodes, key=lambda item: item.index)
            ],
            "instruction": (
                "Generate a complete replacement plan. Preserve a node_key only when the "
                "revised node represents the same logical work."
            ),
        }
        draft = await client.plan(
            json.dumps(revision_context, ensure_ascii=False, separators=(",", ":")),
            contract=contract,
        )
        registry = build_tool_registry(settings)
        capabilities = set(registry.specs())
        for spec in registry.specs().values():
            capabilities.update(spec.capabilities)
        PlanValidator().validate(
            draft,
            task_contract=contract,
            available_capabilities=capabilities,
            budgets=policy.effective.budgets,
        )
        current_by_key = {node.node_key: node for node in current.nodes}
        lineage = {
            node.node_key: current_by_key[node.node_key].id
            for node in draft.nodes
            if node.node_key in current_by_key
        }
        node_state = {
            node.node_key: _preserved_state(current_by_key[node.node_key])
            for node in draft.nodes
            if node.node_key in current_by_key
            and current_by_key[node.node_key].status
            in {PlanNodeStatus.completed.value, PlanNodeStatus.skipped.value}
        }
        revised = await PlanRepository(repository.session).create(
            run_id,
            draft,
            status=PlanStatus.planned,
            supersedes_plan_id=current.id,
            lineage=lineage,
            node_state=node_state,
        )
        return await repository.complete_plan_revision(
            run_id,
            previous_plan=current,
            revised_plan=revised,
        )
    except Exception as exc:
        await repository.session.rollback()
        code = exc.code if isinstance(exc, PlanRevisionError) else "PLAN_REVISION_INVALID"
        await repository.reject_plan_revision(
            run_id,
            plan_id=current_plan_id,
            plan_version=current_plan_version,
            state_version=expected_state_version,
            error_code=code,
        )
        if isinstance(exc, PlanRevisionError):
            raise
        raise PlanRevisionError(str(exc), code=code) from exc


def _dependency_keys(plan, node_id: str) -> list[str]:
    nodes = {node.id: node for node in plan.nodes}
    return sorted(
        nodes[edge.predecessor_id].node_key
        for edge in plan.edges
        if edge.successor_id == node_id and edge.predecessor_id in nodes
    )


def _preserved_state(node) -> dict[str, object]:
    return {
        "status": node.status,
        "evidence_refs": list(node.evidence_refs or []),
        "failure": node.failure,
        "started_at": node.started_at,
        "completed_at": node.completed_at,
    }
