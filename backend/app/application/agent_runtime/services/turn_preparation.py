"""Prepare the canonical plan node and bounded model context for one root turn."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.application.agent_runtime.services.context import (
    ContextAssembler,
    active_node_execution_id,
    active_plan_node_id,
)
from app.application.agent_runtime.services.progress import ExecutionProgress
from app.application.context_compaction.root import compact_root_context
from app.application.planning.scheduler import PlanScheduler
from app.common.core.config import Settings
from app.common.schemas.agent.types import PlanNodeStatus
from app.domain.execution.contracts import SubagentSupervisorPort
from app.infrastructure.db.models.plans import PlanNodeRecord
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.models.skills import RunSkillSnapshotRecord
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.repositories.plans import PlanRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import ToolRegistry
from app.infrastructure.tools.router import ToolRouter


@dataclass(frozen=True)
class PreparedRootTurn:
    model_context: dict[str, Any] | None = None
    active_node: PlanNodeRecord | None = None
    active_node_execution_id: str | None = None
    terminal_status: str | None = None
    terminal_summary: str | None = None


class RootTurnPreparationStage:
    """Refresh shared state and build the read-only input seen by the model."""

    def __init__(
        self,
        *,
        repository: RunUnitOfWork,
        plan_repository: PlanRepository,
        scheduler: PlanScheduler,
        assembler: ContextAssembler,
        settings: Settings,
        model_client: ModelClient,
        tool_registry: ToolRegistry,
        tool_router: ToolRouter,
        progress: ExecutionProgress,
        initial_run: RunRecord,
        initial_skill_snapshot: RunSkillSnapshotRecord | None,
        fresh_run: bool,
        quick_mode: bool,
        subagent_supervisor: SubagentSupervisorPort | None,
    ) -> None:
        self._repository = repository
        self._plans = plan_repository
        self._scheduler = scheduler
        self._assembler = assembler
        self._settings = settings
        self._model_client = model_client
        self._tool_registry = tool_registry
        self._tool_router = tool_router
        self._progress = progress
        self._initial_run = initial_run
        self._initial_skill_snapshot = initial_skill_snapshot
        self._fresh_run = fresh_run
        self._quick_mode = quick_mode
        self._subagents = subagent_supervisor

    async def execute(self, *, run_id: str, goal: str) -> PreparedRootTurn:
        await self._reconcile_subagents(run_id)
        active_node, execution_id = await self._select_active_node(run_id)
        if self._required_plan_node_failed():
            return PreparedRootTurn(
                terminal_status="blocked",
                terminal_summary="活动计划存在失败或阻塞的必需节点。",
            )
        context = await self._assemble_context(run_id, goal)
        return PreparedRootTurn(
            model_context=context,
            active_node=active_node,
            active_node_execution_id=execution_id,
        )

    async def _reconcile_subagents(self, run_id: str) -> None:
        if self._subagents is None:
            return
        current = await self._repository.require_run_core(run_id)
        self._progress.observations.extend(
            await self._subagents.reconcile(parent_state_version=current.state_version)
        )

    async def _select_active_node(
        self,
        run_id: str,
    ) -> tuple[PlanNodeRecord | None, str | None]:
        if self._progress.active_plan is None:
            return None, None
        self._progress.active_plan = await self._plans.active_for_run(run_id)
        current = await self._repository.require_run_core(run_id)
        active_node_id = active_plan_node_id(current.agent_state or {})
        active_node = self._running_node(active_node_id)
        execution_id = active_node_execution_id(current.agent_state or {}, active_node_id)
        if active_node is not None or not self._has_pending_node():
            return active_node, execution_id
        active_node = await self._scheduler.select_next(run_id)
        await self._repository.session.commit()
        self._progress.active_plan = await self._plans.active_for_run(run_id)
        current = await self._repository.require_run_core(run_id)
        execution_id = active_node_execution_id(
            current.agent_state or {},
            active_node.id if active_node else None,
        )
        return active_node, execution_id

    def _running_node(self, active_node_id: str | None) -> PlanNodeRecord | None:
        assert self._progress.active_plan is not None
        return next(
            (
                node
                for node in self._progress.active_plan.nodes
                if node.id == active_node_id and node.status == PlanNodeStatus.running.value
            ),
            None,
        )

    def _has_pending_node(self) -> bool:
        assert self._progress.active_plan is not None
        return any(
            node.status == PlanNodeStatus.pending.value for node in self._progress.active_plan.nodes
        )

    def _required_plan_node_failed(self) -> bool:
        if self._progress.active_plan is None:
            return False
        return any(
            node.status in {PlanNodeStatus.failed.value, PlanNodeStatus.blocked.value}
            and not node.optional
            for node in self._progress.active_plan.nodes
        )

    async def _assemble_context(self, run_id: str, goal: str) -> dict[str, Any]:
        context = await self._assembler.assemble(
            run_id=run_id,
            goal=goal,
            tool_registry=self._tool_registry,
            tool_router=self._tool_router,
            observations=self._progress.observations,
            quick_mode=self._quick_mode,
            initial_run=self._initial_run if self._fresh_run else None,
            initial_skill_snapshot=(self._initial_skill_snapshot if self._fresh_run else None),
        )
        return await compact_root_context(
            repo=self._repository,
            settings=self._settings,
            model_client=self._model_client,
            run_id=run_id,
            goal=goal,
            context=context,
            observations=self._progress.observations,
        )
