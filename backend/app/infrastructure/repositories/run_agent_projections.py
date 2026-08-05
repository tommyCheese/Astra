"""Project persisted agent and node executions into public Run views."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.infrastructure.db.models.executions import AgentExecutionRecord, NodeExecutionRecord
from app.infrastructure.db.models.runs import RunRecord


def agent_execution_tree(
    executions: list[AgentExecutionRecord],
    *,
    agent_plans: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    children = _children_by_parent(executions)
    plans = agent_plans or {}

    def project(execution: AgentExecutionRecord) -> dict[str, Any]:
        view = _agent_execution_view(execution, plans.get(execution.id))
        view["children"] = [project(child) for child in children.get(execution.id, [])]
        return view

    return [project(root) for root in children.get(None, [])]


def _children_by_parent(
    executions: Iterable[AgentExecutionRecord],
) -> dict[str | None, list[AgentExecutionRecord]]:
    children: dict[str | None, list[AgentExecutionRecord]] = {}
    ordered = sorted(
        executions, key=lambda execution: (execution.depth, execution.ordinal, execution.id)
    )
    for execution in ordered:
        children.setdefault(execution.parent_execution_id, []).append(execution)
    return children


def _agent_execution_view(
    execution: AgentExecutionRecord,
    plan: dict[str, Any] | None,
) -> dict[str, Any]:
    request = _mapping_value(execution.contract, "request")
    execution_context = _mapping_value(execution.context_manifest, "execution_context")
    effective_scope = _mapping_value(execution_context, "effective_scope")
    result = execution.result if isinstance(execution.result, dict) else {}
    error = execution.error if isinstance(execution.error, dict) else {}
    return {
        "id": execution.id,
        "parent_execution_id": execution.parent_execution_id,
        "execution_type": execution.execution_type,
        "identity_id": execution.identity_id,
        "delegation_id": execution.delegation_id,
        "request_id": execution.request_id,
        "depth": execution.depth,
        "ordinal": execution.ordinal,
        "objective": request.get("objective"),
        "creation_reason": request.get("relationship") or execution.execution_type,
        "required": not bool(request.get("optional", False)),
        "status": execution.status,
        "phase": execution.phase,
        "wait_reason": execution.wait_reason,
        "budget_envelope": execution.budget_envelope or {},
        "budget_usage": execution.budget_usage or {},
        "permissions": list(effective_scope.get("actions", [])),
        "capabilities": _capability_names(execution.catalog_snapshot),
        "artifact_ids": _artifact_ids(result),
        "result_summary": result.get("summary"),
        "open_issues": list(result.get("open_issues", [])),
        "error": _public_error(error),
        "created_at": execution.created_at,
        "updated_at": execution.updated_at,
        "finished_at": execution.finished_at,
        "plan": plan,
    }


def _mapping_value(source: object, key: str) -> dict[str, Any]:
    if not isinstance(source, dict):
        return {}
    value = source.get(key)
    return value if isinstance(value, dict) else {}


def _capability_names(catalog_snapshot: object) -> list[str]:
    tools = catalog_snapshot.get("tools", []) if isinstance(catalog_snapshot, dict) else []
    return [tool["name"] for tool in tools if isinstance(tool, dict) and tool.get("name")]


def _artifact_ids(result: dict[str, Any]) -> list[str]:
    return [
        artifact["id"]
        for artifact in result.get("artifacts", [])
        if isinstance(artifact, dict) and artifact.get("id")
    ]


def _public_error(error: dict[str, Any]) -> dict[str, Any] | None:
    public_error = {
        key: error[key] for key in ("category", "reason", "message") if error.get(key) is not None
    }
    return public_error or None


def subagent_summary(agent_tree: list[dict[str, Any]]) -> dict[str, Any]:
    subagents = list(_subagents(agent_tree))
    waiting = [agent for agent in subagents if agent.get("status") in _WAITING_STATUSES]
    return {
        "total": len(subagents),
        "running": _count_statuses(subagents, {"queued", "running", "completing"}),
        "waiting": len(waiting),
        "completed": _count_statuses(subagents, {"completed", "completed_with_warnings"}),
        "failed": _count_statuses(subagents, {"failed", "blocked"}),
        "cancelled": _count_statuses(subagents, {"cancelled"}),
        "budget_usage": _combined_budget_usage(subagents),
        "key_wait_reason": next((agent.get("wait_reason") for agent in waiting), None),
    }


_WAITING_STATUSES = frozenset({"waiting_parent", "waiting_approval", "waiting_resource"})


def _subagents(agent_tree: Iterable[dict[str, Any]]) -> Iterable[dict[str, Any]]:
    for agent in agent_tree:
        if agent.get("execution_type") == "child":
            yield agent
        yield from _subagents(agent.get("children", []))


def _count_statuses(agents: Iterable[dict[str, Any]], statuses: set[str]) -> int:
    return sum(agent.get("status") in statuses for agent in agents)


def _combined_budget_usage(agents: Iterable[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for agent in agents:
        for budget_name, usage in agent.get("budget_usage", {}).items():
            if isinstance(usage, int | float):
                totals[budget_name] = totals.get(budget_name, 0) + usage
    return totals


def node_execution_view(execution: NodeExecutionRecord) -> dict[str, Any]:
    return {
        "execution_id": execution.id,
        "run_id": execution.run_id,
        "plan_id": execution.plan_id,
        "plan_node_id": execution.plan_node_id,
        "plan_version": execution.plan_version,
        "attempt": execution.attempt,
        "dispatch_batch_id": execution.dispatch_batch_id,
        "slot_index": execution.slot_index,
        "worker_id": execution.worker_id,
        "phase": execution.phase,
        "status": execution.status,
        "state_version": execution.state_version,
        "checkpoint": execution.checkpoint,
        "wait_reason": execution.wait_reason,
        "started_at": execution.started_at,
        "heartbeat_at": execution.heartbeat_at,
        "finished_at": execution.finished_at,
        "resource_leases": [_resource_lease_view(lease) for lease in execution.resource_leases],
        "budget_reservations": [
            _budget_reservation_view(reservation) for reservation in execution.budget_reservations
        ],
    }


def _resource_lease_view(lease: object) -> dict[str, Any]:
    fields = (
        "id",
        "node_execution_id",
        "resource_summary",
        "mode",
        "fencing_token",
        "acquired_at",
        "expires_at",
        "released_at",
        "release_reason",
    )
    return {field: getattr(lease, field) for field in fields}


def _budget_reservation_view(reservation: object) -> dict[str, Any]:
    fields = (
        "id",
        "node_execution_id",
        "budget_kind",
        "reserved",
        "consumed",
        "status",
        "created_at",
        "settled_at",
    )
    return {field: getattr(reservation, field) for field in fields}


def parallelism_summary(run: RunRecord) -> dict[str, int]:
    executions = list(getattr(run, "node_executions", []))
    active = [execution for execution in executions if execution.status == "active"]
    waiting = [execution for execution in executions if execution.status == "waiting"]
    budgets = ((run.reasoning_policy or {}).get("effective") or {}).get("budgets") or {}
    total_slots = max(1, int(budgets.get("max_parallel_nodes", 3)))
    used_slots = sum(execution.slot_index is not None for execution in [*active, *waiting])
    return {
        "requested_slots": total_slots,
        "total_slots": total_slots,
        "used_slots": min(used_slots, total_slots),
        "active_count": len(active),
        "waiting_count": len(waiting),
    }


def approval_risk_reason(effect_plan: dict[str, Any]) -> str | None:
    risky_effects = [
        effect
        for effect in effect_plan.get("effects", [])
        if isinstance(effect, dict) and _is_risky_effect(effect)
    ]
    if not risky_effects:
        return None
    labels = ", ".join(str(effect.get("kind", "unknown")) for effect in risky_effects)
    return f"该操作包含持久化或不可逆影响：{labels}"


def _is_risky_effect(effect: dict[str, Any]) -> bool:
    return bool(
        effect.get("persistent")
        or effect.get("reversible") is False
        or effect.get("risk") in {"moderate", "high", "critical"}
    )
