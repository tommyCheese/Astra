"""Initialize child execution state and prepare one model turn."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.db.model_base import utc_now
from app.db.models.executions import AgentExecutionRecord, NodeExecutionRecord
from app.db.models.plans import PlanNodeRecord, PlanRecord
from app.db.models.runs import AgentTurnRecord
from app.repositories.agent_executions import AgentExecutionRepository
from app.repositories.plans import PlanRepository
from app.repositories.run_unit_of_work import RunUnitOfWork
from app.schemas.agent.execution_state import AgentDecision
from app.schemas.agent.planning import TaskContract
from app.schemas.context_compaction import ChildCheckpoint, parse_child_checkpoint
from app.schemas.subagents import (
    DelegationContract,
    SubagentArtifactReference,
    SubagentContextCheckpoint,
    SubagentContextManifest,
    SubagentEvidenceReference,
    SubagentExecutionStatus,
)
from app.subagents.executor_contracts import AgentExecutorRuntime
from app.subagents.governance import stable_digest

if TYPE_CHECKING:
    from app.subagents.executor import LocalAstraAgentExecutor


@dataclass
class ChildRunState:
    executions: AgentExecutionRepository
    repository: RunUnitOfWork
    plans: PlanRepository
    execution: AgentExecutionRecord
    task_contract: TaskContract
    plan: PlanRecord
    usage: dict[str, Any]
    observations: list[dict[str, Any]]
    context_checkpoint: ChildCheckpoint
    artifact_refs: list[SubagentArtifactReference] = field(default_factory=list)
    evidence_refs: list[SubagentEvidenceReference] = field(default_factory=list)
    node_execution: NodeExecutionRecord | None = None


@dataclass(frozen=True)
class PreparedChildTurn:
    decision: AgentDecision
    turn: AgentTurnRecord
    active_node: PlanNodeRecord | None
    node_execution: NodeExecutionRecord | None
    model_context: dict[str, Any]


class ChildRunSessionBuilder:
    def __init__(
        self,
        *,
        services: LocalAstraAgentExecutor,
        contract: DelegationContract,
        context_manifest: SubagentContextManifest,
        runtime: AgentExecutorRuntime,
        checkpoint: dict[str, Any] | None,
    ) -> None:
        self._services = services
        self._contract = contract
        self._manifest = context_manifest
        self._runtime = runtime
        self._checkpoint = checkpoint

    async def initialize(self) -> ChildRunState:
        executions = AgentExecutionRepository(self._runtime.session)
        execution = await executions.require(self._runtime.execution_context.agent_execution_id)
        execution = await self._claim_if_queued(executions, execution)
        repository = RunUnitOfWork(self._runtime.session)
        plans = PlanRepository(self._runtime.session)
        usage = deepcopy(execution.budget_usage or {})
        usage.setdefault("model_calls", 0)
        usage.setdefault("tool_calls", 0)
        checkpoint = self._checkpoint or execution.checkpoint or {}
        task_contract = self._services._task_contract(self._contract)
        plan = await self._load_or_create_plan(
            repository,
            plans,
            execution,
            task_contract,
            usage,
        )
        return ChildRunState(
            executions=executions,
            repository=repository,
            plans=plans,
            execution=execution,
            task_contract=task_contract,
            plan=plan,
            usage=usage,
            observations=list(checkpoint.get("observations", [])),
            context_checkpoint=self._context_checkpoint(execution, checkpoint),
        )

    async def prepare_turn(self, state: ChildRunState) -> PreparedChildTurn:
        state.plan = await state.plans.require(state.plan.id)
        active_node = self._services._next_node(state.plan)
        node_execution = None
        if active_node is not None:
            node_execution = await self._services._node_execution(
                self._runtime.session,
                execution=state.execution,
                plan=state.plan,
                node=active_node,
                worker_id=self._runtime.worker_id,
            )
        state.node_execution = node_execution
        model_context = self._model_context(state, active_node)
        decision, _ = await self._services.model_client.decide_with_answer(
            self._contract.request.objective,
            model_context,
        )
        state.usage["model_calls"] += 1
        turn = await state.repository.create_agent_turn(
            self._contract.run_id,
            int(state.usage["model_calls"]),
            decision.decision_type,
            decision.reasoning_summary,
            selected_tool=decision.tool_name,
            decision=decision.model_dump(mode="json"),
            state_version_before=state.execution.state_version,
            plan_version=state.plan.version,
            phase="prepared",
            plan_node_id=active_node.id if active_node else None,
            node_execution_id=node_execution.id if node_execution else None,
            agent_execution_id=state.execution.id,
        )
        return PreparedChildTurn(decision, turn, active_node, node_execution, model_context)

    async def _claim_if_queued(
        self,
        executions: AgentExecutionRepository,
        execution: AgentExecutionRecord,
    ) -> AgentExecutionRecord:
        if execution.status == SubagentExecutionStatus.queued.value:
            execution = await executions.claim(
                execution.id,
                worker_id=self._runtime.worker_id,
                expected_state_version=execution.state_version,
                expected_cancellation_epoch=execution.cancellation_epoch,
            )
            await self._runtime.session.commit()
            return execution
        if execution.status != SubagentExecutionStatus.running.value:
            raise ValueError(f"Child execution is not runnable: {execution.status}")
        return execution

    def _context_checkpoint(
        self,
        execution: AgentExecutionRecord,
        checkpoint: dict[str, Any],
    ) -> ChildCheckpoint:
        raw = checkpoint.get("context_checkpoint")
        if raw:
            return parse_child_checkpoint(raw)
        return SubagentContextCheckpoint(
            agent_execution_id=execution.id,
            manifest_hash=stable_digest(self._manifest.model_dump(mode="json")),
            local_summary="Child execution initialized.",
            created_at=utc_now(),
        )

    async def _load_or_create_plan(
        self,
        repository: RunUnitOfWork,
        plans: PlanRepository,
        execution: AgentExecutionRecord,
        task_contract: TaskContract,
        usage: dict[str, Any],
    ):
        plan = await plans.active_for_run(
            self._contract.run_id,
            agent_execution_id=execution.id,
        )
        if plan is not None:
            return plan
        draft = await self._services.model_client.plan(
            self._contract.request.objective,
            contract=task_contract,
        )
        usage["model_calls"] += 1
        plan = await plans.create(
            self._contract.run_id,
            draft,
            agent_execution_id=execution.id,
        )
        await self._services._event(
            repository,
            self._runtime,
            "subagent.plan.created",
            {"plan_id": plan.id},
        )
        await self._runtime.session.commit()
        return plan

    def _model_context(
        self,
        state: ChildRunState,
        active_node: PlanNodeRecord | None,
    ) -> dict[str, Any]:
        return {
            "agent_execution_id": state.execution.id,
            "delegation_contract": self._contract.model_dump(mode="json"),
            "context_manifest": self._manifest.model_dump(mode="json"),
            "task_contract": state.task_contract.model_dump(mode="json"),
            "plan": self._services._plan_context(state.plan),
            "active_node": self._services._node_context(active_node),
            "observations": deepcopy(state.observations),
            "tool_manifests": {item["name"]: item for item in self._runtime.frozen_catalog.tools},
            "skill_catalog": [deepcopy(item) for item in self._runtime.frozen_catalog.skills],
            "budget": self._contract.request.budget.model_dump(mode="json"),
            "budget_usage": deepcopy(state.usage),
            "continuation_answers": [
                item.model_dump(mode="json")
                for item in state.context_checkpoint.continuation_answers
            ],
            "context_checkpoint": state.context_checkpoint.model_dump(mode="json"),
        }
