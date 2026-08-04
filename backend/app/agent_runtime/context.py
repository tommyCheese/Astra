"""Readable composition of model context from independent projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from app.agent_runtime.context_memory import MemoryContextProjector, MemoryProjection
from app.core.config import Settings
from app.db.models.executions import AgentJoinRecord
from app.db.models.runs import RunRecord
from app.db.models.skills import RunSkillSnapshotRecord
from app.repositories.agent_executions import AgentExecutionRepository
from app.repositories.plans import PlanRepository, plan_to_view
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.repositories.tool_settings import ToolSettingsRepository, default_tool_states
from app.schemas.agent.run_policy import EffectiveSubagentPolicy
from app.schemas.agent.types import AnswerMode
from app.subagents.eligibility import subagent_execution_eligibility
from app.tools.base import ToolRegistry, ToolSpec
from app.tools.router import ToolRouter
from app.tools.selection import CapabilityToolResolver

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


@dataclass(frozen=True)
class ConversationProjection:
    run: RunRecord
    memories: list[Any]
    skill_snapshot: RunSkillSnapshotRecord | None


class ConversationContextLoader:
    def __init__(self, repository: RunUnitOfWork, *, skills_enabled: bool) -> None:
        self._repository = repository
        self._skills_enabled = skills_enabled

    async def load(
        self,
        run_id: str,
        *,
        quick_mode: bool,
        initial_run: RunRecord | None,
        initial_skill_snapshot: RunSkillSnapshotRecord | None,
    ) -> ConversationProjection:
        if quick_mode and initial_run is not None:
            return ConversationProjection(initial_run, [], initial_skill_snapshot)
        if quick_mode:
            run, memories, snapshot = await self._repository.require_run_quick_context(
                run_id,
                include_skills=self._skills_enabled,
            )
            return ConversationProjection(run, list(memories), snapshot)
        run = await self._repository.require_run_core(run_id)
        memories = await self._repository.list_memories(
            run_id=run_id,
            min_confidence=0.0,
            limit=8,
        )
        return ConversationProjection(run, list(memories), None)


@dataclass(frozen=True)
class PlanProjection:
    plan_graph: dict[str, Any]
    active_node_id: str | None
    active_node: dict[str, Any] | None


class PlanContextProjector:
    def __init__(self, repository: RunUnitOfWork) -> None:
        self._repository = repository

    async def project(self, run_id: str, run: RunRecord) -> PlanProjection:
        plan = (
            None
            if run.answer_mode == AnswerMode.standard.value
            else await PlanRepository(self._repository.session).active_for_run(run_id)
        )
        graph = plan_to_view(plan).model_dump(mode="json") if plan else run.plan_graph or {}
        node_id = active_plan_node_id(run.agent_state or {})
        node = next((item for item in graph.get("nodes", []) if item.get("id") == node_id), None)
        return PlanProjection(graph, node_id, node)


@dataclass(frozen=True)
class ToolCatalogProjection:
    manifests: dict[str, dict[str, Any]]
    selection: dict[str, Any]
    unavailable_capabilities: list[Any]
    selected_specs: dict[str, ToolSpec]
    router: ToolRouter


class ToolCatalogProjector:
    async def project(
        self,
        run: RunRecord,
        plan: PlanProjection,
        observations: list[dict[str, Any]],
        registry: ToolRegistry,
        router: ToolRouter | None,
    ) -> ToolCatalogProjection:
        router = router or ToolRouter(
            registry,
            available_backends={spec.execution_backend for spec in registry.specs().values()}
            or {"in_process"},
        )
        resolution = CapabilityToolResolver(router).resolve(
            plan.active_node.get("required_capabilities", []) if plan.active_node else [],
            observations=observations,
            plan_node_id=plan.active_node_id,
        )
        specs = {candidate.tool_name: candidate.spec for candidate in resolution.candidates}
        _, unavailable = router.eligible_specs()
        manifests = {
            name: spec.model_dump(
                include=QUICK_TOOL_MANIFEST_FIELDS
                if run.answer_mode == AnswerMode.standard.value
                else None
            )
            for name, spec in specs.items()
        }
        return ToolCatalogProjection(
            manifests=manifests,
            selection=resolution.audit_payload(),
            unavailable_capabilities=unavailable,
            selected_specs=specs,
            router=router,
        )


@dataclass(frozen=True)
class SkillProjection:
    catalog: list[dict[str, Any]]
    active: list[dict[str, Any]]
    is_draft_test: bool


class SkillContextProjector:
    def __init__(self, repository: RunUnitOfWork, *, enabled: bool) -> None:
        self._repository = repository
        self._enabled = enabled

    async def project(
        self,
        run_id: str,
        snapshot: RunSkillSnapshotRecord | None,
        *,
        quick_mode: bool,
    ) -> SkillProjection:
        if self._enabled and not quick_mode:
            snapshot = await self._repository.session.scalar(
                select(RunSkillSnapshotRecord).where(RunSkillSnapshotRecord.run_id == run_id)
            )
        if snapshot is None:
            return SkillProjection([], [], False)
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
        return SkillProjection(
            catalog,
            [item for item in catalog if item["qualified_identity"] in active_identities],
            bool(snapshot.draft_test),
        )


class SubagentContextProjector:
    def __init__(self, repository: RunUnitOfWork, settings: Settings | None) -> None:
        self._repository = repository
        self._settings = settings

    async def project(
        self,
        run_id: str,
        run: RunRecord,
        selected_specs: dict[str, ToolSpec],
    ) -> tuple[dict[str, Any], bool]:
        raw_policy = ((run.reasoning_policy or {}).get("effective") or {}).get("subagents") or {}
        if not await self._is_executable(raw_policy):
            selected_specs.pop("swarm", None)
            return {}, False
        root = await AgentExecutionRepository(self._repository.session).root_for_run(run_id)
        descendants = (
            await AgentExecutionRepository(self._repository.session).descendants(root.id)
            if root
            else []
        )
        joins = await self._active_joins(root.id) if root else []
        return self._projection(raw_policy, selected_specs.get("swarm"), descendants, joins), True

    async def _is_executable(self, raw_policy: dict[str, Any]) -> bool:
        live_states = (
            await ToolSettingsRepository(self._repository.session).get_or_create(
                default_tool_states(self._settings)
            )
            if self._settings is not None
            else {}
        )
        return subagent_execution_eligibility(
            EffectiveSubagentPolicy.model_validate(raw_policy),
            live_swarm_enabled=bool(live_states.get("swarm", False)),
        ).executable

    @staticmethod
    def _projection(
        raw_policy: dict[str, Any],
        swarm_spec: ToolSpec | None,
        descendants: list[Any],
        joins: list[AgentJoinRecord],
    ) -> dict[str, Any]:
        budgets = raw_policy.get("budgets") or {}
        return {
            "subagent_policy": raw_policy,
            "subagent_capacity": {
                "created_children": len(descendants),
                "active_children": sum(
                    child.status
                    not in {
                        "completed",
                        "completed_with_warnings",
                        "blocked",
                        "failed",
                        "cancelled",
                    }
                    for child in descendants
                ),
                "remaining_children": max(
                    0,
                    int(budgets.get("max_children_total", 0)) - len(descendants),
                ),
                "max_parallel_children": int(budgets.get("max_parallel_children", 0)),
            },
            "subagent_eligible_capabilities": (
                list(swarm_spec.task_capabilities) if swarm_spec else []
            ),
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

    async def _active_joins(self, root_execution_id: str) -> list[AgentJoinRecord]:
        return list(
            (
                await self._repository.session.scalars(
                    select(AgentJoinRecord).where(
                        AgentJoinRecord.parent_execution_id == root_execution_id,
                        AgentJoinRecord.status != "consumed",
                    )
                )
            ).all()
        )


class ContextAssembler:
    def __init__(
        self,
        repository: RunUnitOfWork,
        *,
        skills_enabled: bool = True,
        settings: Settings | None = None,
    ) -> None:
        self._repository = repository
        self._settings = settings
        self._conversation = ConversationContextLoader(
            repository,
            skills_enabled=skills_enabled,
        )
        self._memory = MemoryContextProjector(repository, settings)
        self._plan = PlanContextProjector(repository)
        self._tools = ToolCatalogProjector()
        self._skills = SkillContextProjector(repository, enabled=skills_enabled)
        self._subagents = SubagentContextProjector(repository, settings)

    async def assemble(
        self,
        *,
        run_id: str,
        goal: str,
        tool_registry: ToolRegistry,
        sandbox_provider: Any = None,
        tool_router: ToolRouter | None = None,
        observations: list[dict[str, Any]],
        evidence_pack: dict[str, Any] | None = None,
        quick_mode: bool = False,
        initial_run: RunRecord | None = None,
        initial_skill_snapshot: RunSkillSnapshotRecord | None = None,
    ) -> dict[str, Any]:
        del sandbox_provider
        conversation = await self._conversation.load(
            run_id,
            quick_mode=quick_mode,
            initial_run=initial_run,
            initial_skill_snapshot=initial_skill_snapshot,
        )
        memory = await self._memory.project(run_id, goal, conversation.memories)
        plan = await self._plan.project(run_id, conversation.run)
        tools = await self._tools.project(
            conversation.run,
            plan,
            observations,
            tool_registry,
            tool_router,
        )
        subagents, subagent_enabled = await self._subagents.project(
            run_id,
            conversation.run,
            tools.selected_specs,
        )
        if not subagent_enabled and "swarm" in tools.manifests:
            tools.manifests.pop("swarm", None)
        skills = await self._skills.project(
            run_id,
            conversation.skill_snapshot,
            quick_mode=quick_mode,
        )
        context = self._base_context(
            run_id,
            goal,
            conversation.run,
            observations,
            memory,
            plan,
            tools,
            skills,
        )
        context.update(subagents)
        self._add_optional_context(context, conversation.run, memory, tools, skills, evidence_pack)
        return context

    @staticmethod
    def _base_context(
        run_id: str,
        goal: str,
        run: RunRecord,
        observations: list[dict[str, Any]],
        memory: MemoryProjection,
        plan: PlanProjection,
        tools: ToolCatalogProjection,
        skills: SkillProjection,
    ) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "goal": goal,
            "tool_manifests": tools.manifests,
            "observations": observations,
            "memory_reads": memory.audit_reads,
            "memory_context": memory.context_reads,
            "answer_mode": run.answer_mode,
            "task_contract": run.task_contract or {},
            "plan_graph": plan.plan_graph,
            "active_node": plan.active_node,
            "tool_selection": tools.selection,
            "state_version": run.state_version,
            "plan_version": plan.plan_graph.get("version", 1),
            "skill_catalog": skills.catalog,
            "active_skills": skills.active,
            "subagent_mode": (run.execution_profile or {}).get("subagent_mode", "auto"),
        }

    def _add_optional_context(
        self,
        context: dict[str, Any],
        run: RunRecord,
        memory: MemoryProjection,
        tools: ToolCatalogProjection,
        skills: SkillProjection,
        evidence_pack: dict[str, Any] | None,
    ) -> None:
        if tools.unavailable_capabilities:
            context["unavailable_capabilities"] = tools.unavailable_capabilities
        if memory.recall_event_id:
            context["memory_recall"] = {
                "event_id": memory.recall_event_id,
                "mode": "active",
                "policy_version": (
                    self._settings.agent_memory_retrieval_policy_version if self._settings else None
                ),
            }
        if skills.is_draft_test:
            context["skill_draft_test"] = True
        if run.answer_mode != AnswerMode.standard.value:
            context.update(
                evidence_pack=evidence_pack or {},
                reasoning_policy=run.reasoning_policy or {},
                execution_profile=run.execution_profile or {},
                agent_state=run.agent_state or {},
            )
