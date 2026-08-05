"""Model-assisted revision of waiting Plans."""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.application.planning.service import PlanValidationError, PlanValidator
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.planning import PlanDraft, TaskContract
from app.common.schemas.agent.run_policy import ReasoningPolicySnapshot
from app.common.schemas.agent.types import PlanNodeStatus, PlanStatus
from app.domain.agent_profile import AgentProfile
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.model_clients.contracts import ModelOutputError
from app.infrastructure.model_clients.factory import build_model_client
from app.infrastructure.model_clients.usage_metering import DatabaseUsageRecorder
from app.infrastructure.repositories.plans import PlanRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.registry import build_tool_registry
from app.infrastructure.tools.selection import forbidden_plan_bindings, task_capability_catalog


class PlanRevisionError(ValueError):
    def __init__(self, message: str, *, code: str = "PLAN_REVISION_INVALID"):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RevisionEnvironment:
    contract: TaskContract
    policy: ReasoningPolicySnapshot
    validator: PlanValidator
    available_capabilities: set[str]
    forbidden_capabilities: set[str]
    criterion_ids: list[str]
    prompt_context: dict[str, object]


async def revise_waiting_plan(
    repository: RunUnitOfWork,
    settings: AstraRuntimeSettings,
    *,
    run_id: str,
    request: str,
    continuation_token: str,
    plan_id: str,
    expected_plan_version: int,
    expected_state_version: int,
) -> RunRecord:
    client = None
    run, current = await repository.claim_plan_revision(
        run_id,
        continuation_token=continuation_token,
        plan_id=plan_id,
        expected_plan_version=expected_plan_version,
        expected_state_version=expected_state_version,
    )
    # Persist revision ownership before the model wait so recovery can restore it.
    await repository.commit()
    current_plan_id = current.id
    current_plan_version = current.version
    try:
        client = build_model_client(settings)
        environment = _build_revision_environment(run, current, settings, request)
        _configure_revision_client(client, run, environment.policy, run_id)
        draft = await _request_validated_draft(client, environment)
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
    finally:
        if client is not None:
            await client.aclose()


def _configure_revision_client(client, run, policy, run_id: str) -> None:
    if hasattr(client, "usage_recorder"):
        client.usage_recorder = DatabaseUsageRecorder(run_id)
    client.bind_agent_profile(AgentProfile.from_snapshot(run.agent_profile_snapshot))
    client.bind_reasoning_effort(policy.effective.reasoning_effort)
    client.bind_model_thinking((run.model_policy or {}).get("thinking"))


def _build_revision_environment(run, current, settings, request: str) -> RevisionEnvironment:
    contract = TaskContract.model_validate(run.task_contract)
    policy = ReasoningPolicySnapshot.model_validate(run.reasoning_policy)
    tool_specs = build_tool_registry(settings).specs()
    capabilities = task_capability_catalog(tool_specs)
    criterion_ids = [criterion.id for criterion in contract.success_criteria]
    prompt_context = {
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
            "Generate a complete replacement plan. Preserve a node_key only when the revised "
            "node represents the same logical work. Use only supplied success criterion IDs "
            "and provider-neutral task capabilities. Never bind a Plan node to a concrete tool, "
            "provider, permission, executor, or backend. Satisfy every listed capability and "
            "keep the dependency graph acyclic and within the supplied maximum depth."
        ),
        "validation_constraints": {
            "success_criteria_ids": criterion_ids,
            "available_capabilities": sorted(capabilities),
            "maximum_plan_depth": policy.effective.budgets.max_plan_depth,
            "maximum_nodes": max(1, policy.effective.budgets.max_plan_depth * 4),
        },
    }
    return RevisionEnvironment(
        contract=contract,
        policy=policy,
        validator=PlanValidator(),
        available_capabilities=capabilities,
        forbidden_capabilities=forbidden_plan_bindings(tool_specs),
        criterion_ids=criterion_ids,
        prompt_context=prompt_context,
    )


async def _request_validated_draft(client, environment: RevisionEnvironment) -> PlanDraft:
    validation_error: str | None = None
    for attempt in range(2):
        attempt_context = dict(environment.prompt_context)
        if validation_error:
            attempt_context["validation_feedback"] = (
                f"The previous replacement plan was rejected: {validation_error}. "
                "Return a corrected complete PlanDraft."
            )
        try:
            candidate = await client.plan(
                json.dumps(attempt_context, ensure_ascii=False, separators=(",", ":")),
                contract=environment.contract,
            )
            normalized = _normalize_revision_metadata(
                candidate,
                criterion_ids=environment.criterion_ids,
            )
            return environment.validator.validate(
                normalized,
                task_contract=environment.contract,
                available_capabilities=environment.available_capabilities,
                forbidden_capabilities=environment.forbidden_capabilities,
                budgets=environment.policy.effective.budgets,
            )
        except (ModelOutputError, PlanValidationError) as error:
            validation_error = str(error)
            if attempt == 1:
                raise
    raise PlanRevisionError("Plan revision did not produce a validated draft")


def _dependency_keys(plan, node_id: str) -> list[str]:
    nodes = {node.id: node for node in plan.nodes}
    return sorted(
        nodes[edge.predecessor_id].node_key
        for edge in plan.edges
        if edge.successor_id == node_id and edge.predecessor_id in nodes
    )


def _normalize_revision_metadata(
    draft: PlanDraft,
    *,
    criterion_ids: list[str],
) -> PlanDraft:
    valid_criteria = set(criterion_ids)
    nodes = []
    for node in draft.nodes:
        criteria = [
            criterion for criterion in node.success_criteria_refs if criterion in valid_criteria
        ] or list(criterion_ids)
        nodes.append(
            node.model_copy(
                update={
                    "success_criteria_refs": list(dict.fromkeys(criteria)),
                    "required_capabilities": list(dict.fromkeys(node.required_capabilities)),
                }
            )
        )
    return draft.model_copy(update={"nodes": nodes})


def _preserved_state(node) -> dict[str, object]:
    return {
        "status": node.status,
        "evidence_refs": list(node.evidence_refs or []),
        "failure": node.failure,
        "started_at": node.started_at,
        "completed_at": node.completed_at,
    }
