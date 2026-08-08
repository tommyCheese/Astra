"""Assemble model context without intermediate projector objects."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.application.agent_runtime.services.context.memory import (
    AgentMemoryContextProjection,
    MemoryContextProjector,
)
from app.application.subagents.eligibility import subagent_execution_eligibility
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_policy import EffectiveSubagentPolicy
from app.common.schemas.agent.types import AnswerMode
from app.infrastructure.db.models.executions import AgentJoinRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.models.skills import RunSkillSnapshotRecord
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.plans import PlanRepository, plan_to_view
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.tool_settings import (
    ToolSettingsRepository,
    default_tool_states,
)
from app.infrastructure.tools.base import AstraToolRegistry, AstraToolSpec
from app.infrastructure.tools.router import ToolRouter
from app.infrastructure.tools.selection import CapabilityToolResolver

QUICK_TOOL_MANIFEST_FIELDS = {
    "description",
    "input_schema",
    "permission",
    "side_effect_level",
    "task_capabilities",
    "capabilities",
    "permissions",
    "risk",
}


def active_plan_node_id(agent_state: dict[str, Any]) -> str | None:
    active_executions = [
        execution
        for execution in agent_state.get("active_executions", [])
        if isinstance(execution, dict) and execution.get("status") in {None, "active", "waiting"}
    ]
    if not active_executions:
        return None
    selected = min(
        active_executions,
        key=lambda execution: (
            execution.get("slot_index") is None,
            execution.get("slot_index") if execution.get("slot_index") is not None else 10_000,
            str(execution.get("plan_node_id") or ""),
        ),
    )
    return selected.get("plan_node_id")


def active_node_execution_id(
    agent_state: dict[str, Any],
    plan_node_id: str | None,
) -> str | None:
    if plan_node_id is None:
        return None
    for execution in agent_state.get("active_executions", []):
        if (
            isinstance(execution, dict)
            and execution.get("plan_node_id") == plan_node_id
            and execution.get("status") in {None, "active", "waiting"}
        ):
            execution_id = execution.get("execution_id")
            return str(execution_id) if execution_id else None
    return None


async def _load_conversation(
    repository: RunUnitOfWork,
    run_id: str,
    *,
    skills_enabled: bool,
    legacy_standard_mode: bool,
    initial_run: RunRecord | None,
    initial_skill_snapshot: RunSkillSnapshotRecord | None,
) -> tuple[RunRecord, list[Any], RunSkillSnapshotRecord | None]:
    if legacy_standard_mode and initial_run is not None:
        return initial_run, [], initial_skill_snapshot
    if legacy_standard_mode:
        run, memories, snapshot = await repository.require_run_quick_context(
            run_id,
            include_skills=skills_enabled,
        )
        return run, list(memories), snapshot
    run = await repository.require_run_core(run_id)
    memories = await repository.list_memories(run_id=run_id, min_confidence=0.0, limit=8)
    return run, list(memories), None


async def _load_plan(
    repository: RunUnitOfWork,
    run_id: str,
    run: RunRecord,
) -> tuple[dict[str, Any], str | None, dict[str, Any] | None]:
    plan = (
        None
        if run.answer_mode == AnswerMode.standard.value
        else await PlanRepository(repository.session).active_for_run(run_id)
    )
    graph = plan_to_view(plan).model_dump(mode="json") if plan else run.plan_graph or {}
    node_id = active_plan_node_id(run.agent_state or {})
    node = next((item for item in graph.get("nodes", []) if item.get("id") == node_id), None)
    return graph, node_id, node


def _load_tools(
    run: RunRecord,
    active_node_id: str | None,
    active_node: dict[str, Any] | None,
    observations: list[dict[str, Any]],
    registry: AstraToolRegistry,
    router: ToolRouter | None,
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, Any],
    list[Any],
    dict[str, AstraToolSpec],
]:
    router = router or ToolRouter(
        registry,
        available_backends={spec.execution_backend for spec in registry.specs().values()}
        or {"in_process"},
    )
    resolution = CapabilityToolResolver(router).resolve(
        active_node.get("required_capabilities", []) if active_node else [],
        observations=observations,
        plan_node_id=active_node_id,
    )
    specs = {candidate.tool_name: candidate.spec for candidate in resolution.candidates}
    _, unavailable = router.eligible_specs()
    manifests = {
        name: spec.model_dump(
            include=(
                QUICK_TOOL_MANIFEST_FIELDS
                if run.answer_mode == AnswerMode.standard.value
                else None
            )
        )
        for name, spec in specs.items()
    }
    return manifests, resolution.audit_payload(), unavailable, specs


async def _load_skills(
    repository: RunUnitOfWork,
    run_id: str,
    snapshot: RunSkillSnapshotRecord | None,
    *,
    enabled: bool,
    legacy_standard_mode: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    if enabled and not legacy_standard_mode:
        snapshot = await repository.session.scalar(
            select(RunSkillSnapshotRecord).where(RunSkillSnapshotRecord.run_id == run_id)
        )
    if snapshot is None:
        return [], [], False
    active_identities = {
        activation["qualified_identity"] for activation in snapshot.activations or []
    }
    catalog = [
        {
            "qualified_identity": item["qualified_identity"],
            "name": item["name"],
            "description": item["description"],
            "origin": item["origin"],
            "revision_id": item["revision_id"],
            "digest": item["digest"],
        }
        for item in snapshot.catalog
    ]
    return (
        catalog,
        [item for item in catalog if item["qualified_identity"] in active_identities],
        bool(snapshot.draft_test),
    )


async def _subagents_executable(
    repository: RunUnitOfWork,
    settings: AstraRuntimeSettings | None,
    raw_policy: dict[str, Any],
) -> bool:
    live_states = (
        await ToolSettingsRepository(repository.session).get_or_create(
            default_tool_states(settings)
        )
        if settings is not None
        else {}
    )
    return subagent_execution_eligibility(
        EffectiveSubagentPolicy.model_validate(raw_policy),
        live_swarm_enabled=bool(live_states.get("swarm", False)),
    ).executable


async def _active_joins(
    repository: RunUnitOfWork,
    root_execution_id: str,
) -> list[AgentJoinRecord]:
    return list(
        (
            await repository.session.scalars(
                select(AgentJoinRecord).where(
                    AgentJoinRecord.parent_execution_id == root_execution_id,
                    AgentJoinRecord.status != "consumed",
                )
            )
        ).all()
    )


def _subagent_projection(
    raw_policy: dict[str, Any],
    swarm_spec: AstraToolSpec | None,
    descendants: list[Any],
    joins: list[AgentJoinRecord],
) -> dict[str, Any]:
    budgets = raw_policy.get("budgets") or {}
    terminal = {"completed", "completed_with_warnings", "blocked", "failed", "cancelled"}
    return {
        "subagent_policy": raw_policy,
        "subagent_capacity": {
            "created_children": len(descendants),
            "active_children": sum(child.status not in terminal for child in descendants),
            "remaining_children": max(
                0,
                int(budgets.get("max_children_total", 0)) - len(descendants),
            ),
            "max_parallel_children": int(budgets.get("max_parallel_children", 0)),
        },
        "subagent_eligible_capabilities": list(swarm_spec.task_capabilities)
        if swarm_spec
        else [],
        "subagent_active_groups": [
            {
                "group_id": join.group_id,
                "join_id": join.id,
                "status": join.status,
                "policy": join.policy,
                "child_execution_ids": list(join.child_execution_ids),
                "consumer_plan_node_id": join.consumer_plan_node_id,
            }
            for join in joins
        ],
    }


async def _load_subagents(
    repository: RunUnitOfWork,
    settings: AstraRuntimeSettings | None,
    run_id: str,
    run: RunRecord,
    selected_specs: dict[str, AstraToolSpec],
) -> tuple[dict[str, Any], bool]:
    raw_policy = ((run.reasoning_policy or {}).get("effective") or {}).get("subagents") or {}
    if not await _subagents_executable(repository, settings, raw_policy):
        selected_specs.pop("swarm", None)
        return {}, False
    executions = AgentExecutionRepository(repository.session)
    root = await executions.root_for_run(run_id)
    descendants = await executions.descendants(root.id) if root else []
    joins = await _active_joins(repository, root.id) if root else []
    return _subagent_projection(raw_policy, selected_specs.get("swarm"), descendants, joins), True


class AgentContextAssembler:
    def __init__(
        self,
        repository: RunUnitOfWork,
        *,
        skills_enabled: bool = True,
        settings: AstraRuntimeSettings | None = None,
    ) -> None:
        self._repository = repository
        self._skills_enabled = skills_enabled
        self._settings = settings
        self._memory = MemoryContextProjector(repository, settings)

    async def assemble(
        self,
        *,
        run_id: str,
        goal: str,
        tool_registry: AstraToolRegistry,
        sandbox_provider: Any = None,
        tool_router: ToolRouter | None = None,
        observations: list[dict[str, Any]],
        evidence_pack: dict[str, Any] | None = None,
        legacy_standard_mode: bool = False,
        initial_run: RunRecord | None = None,
        initial_skill_snapshot: RunSkillSnapshotRecord | None = None,
    ) -> dict[str, Any]:
        del sandbox_provider
        run, memories, skill_snapshot = await _load_conversation(
            self._repository,
            run_id,
            skills_enabled=self._skills_enabled,
            legacy_standard_mode=legacy_standard_mode,
            initial_run=initial_run,
            initial_skill_snapshot=initial_skill_snapshot,
        )
        memory = await self._memory.project(run_id, goal, memories)
        plan_graph, active_node_id, active_node = await _load_plan(
            self._repository, run_id, run
        )
        tool_manifests, tool_selection, unavailable, selected_specs = _load_tools(
            run,
            active_node_id,
            active_node,
            observations,
            tool_registry,
            tool_router,
        )
        subagents, subagent_enabled = await _load_subagents(
            self._repository,
            self._settings,
            run_id,
            run,
            selected_specs,
        )
        if not subagent_enabled:
            tool_manifests.pop("swarm", None)
        skill_catalog, active_skills, is_draft_test = await _load_skills(
            self._repository,
            run_id,
            skill_snapshot,
            enabled=self._skills_enabled,
            legacy_standard_mode=legacy_standard_mode,
        )
        context = {
            "run_id": run_id,
            "goal": goal,
            "tool_manifests": tool_manifests,
            "observations": observations,
            "memory_reads": memory.audit_reads,
            "memory_context": memory.context_reads,
            "answer_mode": run.answer_mode,
            "task_contract": run.task_contract or {},
            "plan_graph": plan_graph,
            "active_node": active_node,
            "tool_selection": tool_selection,
            "state_version": run.state_version,
            "plan_version": plan_graph.get("version", 1),
            "skill_catalog": skill_catalog,
            "active_skills": active_skills,
            "subagent_mode": (run.execution_profile or {}).get("subagent_mode", "auto"),
            **subagents,
        }
        self._add_optional_context(
            context,
            run,
            memory,
            unavailable,
            is_draft_test,
            evidence_pack,
        )
        return context

    def _add_optional_context(
        self,
        context: dict[str, Any],
        run: RunRecord,
        memory: AgentMemoryContextProjection,
        unavailable_capabilities: list[Any],
        is_draft_test: bool,
        evidence_pack: dict[str, Any] | None,
    ) -> None:
        if unavailable_capabilities:
            context["unavailable_capabilities"] = unavailable_capabilities
        if memory.recall_event_id:
            context["memory_recall"] = {
                "event_id": memory.recall_event_id,
                "mode": "active",
                "policy_version": (
                    self._settings.agent_memory_retrieval_policy_version
                    if self._settings
                    else None
                ),
            }
        if is_draft_test:
            context["skill_draft_test"] = True
        if run.answer_mode != AnswerMode.standard.value:
            context.update(
                agent_profile_snapshot=run.agent_profile_snapshot or {},
                evidence_pack=evidence_pack or {},
                reasoning_policy=run.reasoning_policy or {},
                execution_profile=run.execution_profile or {},
                agent_state=run.agent_state or {},
            )
