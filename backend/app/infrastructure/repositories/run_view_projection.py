"""Compose persisted Run records into the public Run read model."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.common.schemas.agent.api_views import RunView
from app.common.schemas.agent.run_result import RunResult
from app.infrastructure.db.models.permissions import ApprovalRequestRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.repositories.plans import plan_to_summary, plan_to_view
from app.infrastructure.repositories.run_agent_projections import (
    agent_execution_tree,
    approval_risk_reason,
    node_execution_view,
    parallelism_summary,
    subagent_summary,
)
from app.infrastructure.repositories.run_chat_projection import build_chat_messages
from app.infrastructure.repositories.run_query_store import safe_agent_profile_manifest
from app.infrastructure.repositories.run_record_projections import (
    artifact_views,
    event_views,
    join_views,
    memory_views,
    sandbox_job_views,
    step_views,
    tool_call_views,
    turn_views,
)


class RunViewProjector:
    """Own all mapping from persistence records to the public Run schema."""

    def project(self, run: RunRecord) -> RunView:
        return RunView.model_validate(self.payload(run))

    def project_initial(self, run: RunRecord) -> RunView:
        return RunView.model_validate(self.initial_payload(run))

    def payload(self, run: RunRecord) -> dict[str, Any]:
        canonical_steps, plan_payload, plan_versions = _plan_projection(run)
        execution_views = [node_execution_view(execution) for execution in run.node_executions]
        parallelism = parallelism_summary(run)
        agent_tree = _agent_tree(run)
        pending_approval = next(
            (request for request in reversed(run.approval_requests) if request.status == "pending"),
            None,
        )
        return {
            **_run_identity(run),
            **_run_content(run, canonical_steps),
            **_run_policies(run, plan_payload, plan_versions),
            "pending_approval": _pending_approval_view(pending_approval),
            "node_executions": execution_views,
            "parallelism": parallelism,
            "agent_executions": agent_tree,
            "agent_joins": join_views(run),
            "subagent_summary": subagent_summary(agent_tree),
            "task_adapter": run.task_adapter or "web",
            "agent_profile": safe_agent_profile_manifest(run.agent_profile_snapshot or {}),
        }

    def initial_payload(self, run: RunRecord) -> dict[str, Any]:
        trusted = _is_trusted(run)
        return {
            **_run_identity(run),
            **_empty_run_content(run),
            **_run_policies(run, (run.plan_graph or {}) if trusted else {}, []),
            "pending_approval": None,
            "node_executions": [],
            "parallelism": None,
            "agent_executions": [],
            "agent_joins": [],
            "subagent_summary": _empty_subagent_summary(),
            "task_adapter": run.task_adapter or "web",
            "agent_profile": safe_agent_profile_manifest(run.agent_profile_snapshot or {}),
        }


def _plan_projection(
    run: RunRecord,
) -> tuple[list[dict[str, Any]] | None, dict[str, Any], list[dict[str, Any]]]:
    if not _is_trusted(run):
        return None, {}, []
    active_plan = _active_plan(run)
    plan_view = plan_to_view(active_plan) if active_plan is not None else None
    return (
        _canonical_steps(plan_view, active_plan),
        _plan_payload(run, plan_view),
        _plan_versions(run),
    )


def _active_plan(run: RunRecord):
    return next(
        (plan for plan in getattr(run, "plans", []) if plan.id == run.active_plan_id),
        None,
    )


def _plan_payload(run: RunRecord, plan_view: object) -> dict[str, Any]:
    payload = plan_view.model_dump(mode="json") if plan_view else run.plan_graph or {}
    if not plan_view:
        return payload
    execution_views = [node_execution_view(execution) for execution in run.node_executions]
    return {
        **payload,
        "active_executions": [
            execution
            for execution in execution_views
            if execution["status"] in {"active", "waiting"}
        ],
        "parallelism": parallelism_summary(run),
    }


def _plan_versions(run: RunRecord) -> list[dict[str, Any]]:
    return [
        plan_to_summary(plan).model_dump(mode="json")
        for plan in sorted(getattr(run, "plans", []), key=lambda plan: plan.version)
    ]


def _canonical_steps(plan_view: object, active_plan: object) -> list[dict[str, Any]] | None:
    if plan_view is None or active_plan is None:
        return None
    persisted_nodes = {node.id: node for node in active_plan.nodes}
    return [_canonical_step(node, persisted_nodes[node.id]) for node in plan_view.nodes]


def _canonical_step(node: object, persisted_node: object) -> dict[str, Any]:
    expected_outcome = node.expected_outcome
    return {
        "id": node.id,
        "plan_id": node.plan_id,
        "plan_version": node.plan_version,
        "node_key": node.node_key,
        "index": node.index,
        "title": node.title,
        "intent": node.intent,
        "status": node.status.value,
        "depends_on": node.depends_on,
        "required_capabilities": node.required_capabilities,
        "required_skill_ids": node.required_skill_ids,
        "success_criteria_refs": node.success_criteria_refs,
        "expected_outcome": expected_outcome.model_dump(mode="json") if expected_outcome else None,
        "risk_level": node.risk_level,
        "optional": node.optional,
        "evidence_refs": node.evidence_refs,
        "evidence": {"refs": node.evidence_refs} if node.evidence_refs else None,
        "failure": node.failure,
        "started_at": persisted_node.started_at,
        "completed_at": persisted_node.completed_at,
    }


def _agent_tree(run: RunRecord) -> list[dict[str, Any]]:
    plans = {
        plan.agent_execution_id: plan_to_view(plan).model_dump(mode="json")
        for plan in getattr(run, "plans", [])
        if plan.agent_execution_id is not None and plan.status in {"planned", "active", "completed"}
    }
    return agent_execution_tree(list(getattr(run, "agent_executions", [])), agent_plans=plans)


def _run_identity(run: RunRecord) -> dict[str, Any]:
    return {
        "id": run.id,
        "task_id": run.task_id,
        "status": run.status,
        "mode": run.mode,
        "processing_duration_ms": _processing_duration_ms(run),
        "answer_mode": run.answer_mode or "trusted",
        "execution_profile": run.execution_profile or {},
        "summary": run.summary,
        "result": _result_view(run),
    }


def _processing_duration_ms(run: RunRecord) -> int | None:
    if run.completed_at is None:
        return None
    started_at = run.started_at or run.created_at
    completed_at = run.completed_at.replace(tzinfo=None)
    normalized_started_at = started_at.replace(tzinfo=None)
    return max(0, int((completed_at - normalized_started_at).total_seconds() * 1000))


def _result_view(run: RunRecord) -> dict[str, Any] | None:
    if run.result is None:
        return None
    raw_result = dict(run.result) if isinstance(run.result, dict) else {}
    raw_result.setdefault("summary", run.summary or "")
    return RunResult.model_validate(raw_result).model_dump(mode="json")


def _run_content(
    run: RunRecord,
    canonical_steps: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return {
        "steps": step_views(run, canonical_steps),
        "tool_calls": tool_call_views(run),
        "artifacts": artifact_views(run),
        "sandbox_jobs": sandbox_job_views(run),
        "events": event_views(run),
        "turns": turn_views(run),
        "memories": memory_views(run),
        "chat_messages": build_chat_messages(run),
    }


def _empty_run_content(run: RunRecord) -> dict[str, Any]:
    goal = str((run.model_policy or {}).get("conversation_goal") or "")
    command = (
        "/subagent" if (run.execution_profile or {}).get("subagent_mode") == "required" else ""
    )
    visible_goal = f"{command} {goal}" if command else goal
    return {
        "steps": [],
        "tool_calls": [],
        "artifacts": [],
        "sandbox_jobs": [],
        "events": [],
        "turns": [],
        "memories": [],
        "chat_messages": [
            {
                "id": f"{run.id}-user",
                "role": "user",
                "content": visible_goal,
                "status": "completed",
                "metadata": {"task_id": run.task_id, **({"command": command} if command else {})},
            }
        ],
    }


def _run_policies(
    run: RunRecord,
    plan_payload: dict[str, Any],
    plan_versions: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "model_policy": _public_model_policy(run.model_policy),
        "reasoning_policy": run.reasoning_policy or {},
        "task_contract": run.task_contract or {},
        "plan_graph": plan_payload,
        "plan_versions": plan_versions,
        "agent_state": run.agent_state or {},
        "state_version": run.state_version or 0,
        "terminal_reason": run.terminal_reason,
        "waiting_state": run.waiting_state,
    }


def _public_model_policy(model_policy: dict[str, Any] | None) -> dict[str, Any]:
    policy = model_policy or {}
    return {
        key: deepcopy(policy[key])
        for key in ("provider", "model", "thinking", "context")
        if key in policy
    }


def _pending_approval_view(request: ApprovalRequestRecord | None) -> dict[str, Any] | None:
    if request is None:
        return None
    effect_plan = request.frozen_effect_plan
    similar_decisions = ["allow_similar", "allow_task"] if request.similar_matcher else []
    return {
        "id": request.id,
        "tool_call_id": request.tool_call_id,
        "node_execution_id": request.node_execution_id,
        "execution_attempt": request.execution_attempt,
        "expected_execution_state_version": request.expected_execution_state_version,
        "tool_name": request.tool_name,
        "preview": request.preview,
        "permission": request.permission,
        "impact": request.impact,
        "action_summary": effect_plan.get("summary"),
        "affected_resources": _effect_values(effect_plan, "resource"),
        "risk_reason": approval_risk_reason(effect_plan),
        "working_directory": effect_plan.get("cwd"),
        "network_scope": effect_plan.get("network_scope", {}),
        "effect_kinds": _effect_values(effect_plan, "kind"),
        "grant_proposals": _grant_proposals(request.similar_matcher),
        "reviewer_identity": request.reviewer_identity,
        "decisions": ["approve_once", *similar_decisions, "reject"],
        "created_at": request.created_at,
    }


def _effect_values(effect_plan: dict[str, Any], key: str) -> list[Any]:
    return [
        effect[key]
        for effect in effect_plan.get("effects", [])
        if isinstance(effect, dict) and effect.get(key)
    ]


def _grant_proposals(matcher: dict[str, Any] | None) -> list[dict[str, Any]]:
    if matcher is None:
        return []
    return [{**matcher, "scope": "run"}, {**matcher, "scope": "task"}]


def _empty_subagent_summary() -> dict[str, Any]:
    return {
        "total": 0,
        "running": 0,
        "waiting": 0,
        "completed": 0,
        "failed": 0,
        "cancelled": 0,
        "budget_usage": {},
        "key_wait_reason": None,
    }


def _is_trusted(run: RunRecord) -> bool:
    return (run.answer_mode or "trusted") == "trusted"
