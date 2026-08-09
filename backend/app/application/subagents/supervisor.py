from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.subagents.coordinator import AgentCoordinator, HierarchicalSemaphoreRegistry
from app.application.subagents.eligibility import subagent_execution_eligibility
from app.application.subagents.executor import LocalAstraAgentExecutor
from app.application.subagents.fan_in import SubagentJoinService, merge_subagent_results
from app.application.subagents.operations import SubagentRuntimeOperations
from app.application.workspaces.artifacts import ArtifactService, LocalArtifactStore
from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.agent.run_policy import EffectiveSubagentPolicy
from app.common.schemas.subagents import (
    DelegationContract,
    SubagentContextManifest,
    SubagentExecutionStatus,
    SubagentFanoutRequest,
    SubagentFanoutResult,
)
from app.infrastructure.db.models.executions import AgentExecutionRecord
from app.infrastructure.model_clients.usage_metering import DatabaseUsageRecorder
from app.infrastructure.repositories.agent_executions import AgentExecutionRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.repositories.tool_settings import (
    ToolSettingsRepository,
    default_tool_states,
)
from app.infrastructure.tools.base import AstraToolRegistry

logger = logging.getLogger("astra.subagent_supervisor")
_SHARED_SUBAGENT_SEMAPHORES = HierarchicalSemaphoreRegistry()


class SubagentSupervisor:
    """Run-scoped control plane for durable fan-out, workers, and Join consumption."""

    def __init__(
        self,
        *,
        settings: AstraRuntimeSettings,
        session: AsyncSession,
        session_factory: async_sessionmaker[AsyncSession],
        run_id: str,
        parent_execution_id: str,
        parent_identity_id: str,
        policy: EffectiveSubagentPolicy,
        tool_registry: AstraToolRegistry,
        model_client_factory,
    ):
        self.settings = settings
        self.session = session
        self.session_factory = session_factory
        self.run_id = run_id
        self.parent_execution_id = parent_execution_id
        self.parent_identity_id = parent_identity_id
        self.policy = policy
        self.tool_registry = tool_registry
        self.model_client_factory = model_client_factory
        self.operations = SubagentRuntimeOperations(session, policy=policy)
        self.coordinator = AgentCoordinator(
            session_factory,
            deployment_max_agents=settings.agent_provider_concurrency_limit,
            run_max_agents=policy.budgets.max_parallel_children,
            run_max_nodes=max(
                policy.budgets.max_parallel_children,
                settings.agent_max_parallel_nodes,
            ),
            heartbeat_seconds=settings.agent_execution_heartbeat_seconds,
            semaphore_registry=_SHARED_SUBAGENT_SEMAPHORES,
        )
        self._dispatch_task: asyncio.Task[None] | None = None
        self._dispatch_lock = asyncio.Lock()
        self._closed = False
        self._reported_blocked_joins: set[str] = set()
        self._reported_consumed_joins: set[str] = set()

    async def delegate_tasks(self, fanout: SubagentFanoutRequest) -> SubagentFanoutResult:
        if self._closed:
            raise ValueError("Subagent supervisor is closed")
        live_tool_states = await ToolSettingsRepository(self.session).get_or_create(default_tool_states(self.settings))
        eligibility = subagent_execution_eligibility(
            self.policy,
            live_swarm_enabled=bool(live_tool_states.get("swarm", False)),
        )
        if not eligibility.executable:
            raise ValueError(eligibility.message)
        result = await self.operations.delegate_tasks(
            parent_execution_id=self.parent_execution_id,
            parent_identity_id=self.parent_identity_id,
            fanout=fanout,
        )
        await self.wake()
        return result

    async def wake(self) -> None:
        """Start a background drain if one is not already active."""
        async with self._dispatch_lock:
            if self._closed:
                return
            if self._dispatch_task is None or self._dispatch_task.done():
                self._dispatch_task = asyncio.create_task(self._drain_queue(), name=f"subagent-supervisor:{self.run_id}")

    async def _drain_queue(self) -> None:
        while not self._closed:
            result = await self.coordinator.run_available(
                self.run_id,
                self._run_child,
                provider=self.settings.model_provider,
                provider_limit=self.settings.agent_provider_concurrency_limit,
                tool_group="subagent-read-only",
                tool_limit=self.settings.agent_capability_concurrency_limit,
                capability="delegated-agent",
                capability_limit=self.settings.agent_capability_concurrency_limit,
            )
            if not result.claimed_ids:
                return
            logger.info(
                "subagent.dispatch.completed run_id=%s claimed=%s completed=%s failed=%s peak=%s",
                self.run_id,
                len(result.claimed_ids),
                len(result.completed_ids),
                len(result.failed_ids),
                result.peak_concurrency,
            )

    async def _run_child(
        self,
        session: AsyncSession,
        execution: AgentExecutionRecord,
        _fencing_token: int,
    ) -> None:
        model_client = self.model_client_factory()
        if hasattr(model_client, "usage_recorder"):
            model_client.usage_recorder = DatabaseUsageRecorder(execution.run_id, agent_execution_id=execution.id)
        executor = LocalAstraAgentExecutor(
            model_client=model_client,
            tool_registry=self.tool_registry,
            settings=self.settings,
        )
        operations = SubagentRuntimeOperations(session, policy=self.policy)
        runtime = await operations.executor_runtime(
            execution.id,
            worker_id=execution.worker_id or f"subagent:{execution.id}",
            artifact_service=ArtifactService(
                RunUnitOfWork(session),
                LocalArtifactStore(self.settings.artifact_store_path),
                max_files=self.settings.artifact_max_files,
                max_bytes=self.settings.artifact_max_bytes,
            ),
        )
        stored = execution.context_manifest or {}
        contract = DelegationContract.model_validate(execution.contract)
        manifest = SubagentContextManifest.model_validate(stored["manifest"])
        try:
            await executor.execute(
                contract=contract,
                context_manifest=manifest,
                runtime=runtime,
                checkpoint=execution.checkpoint,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "subagent.worker.failed run_id=%s execution_id=%s",
                execution.run_id,
                execution.id,
            )
            await session.rollback()
            repository = AgentExecutionRepository(session)
            current = await repository.require(execution.id)
            if current.status == SubagentExecutionStatus.running.value:
                await repository.transition(
                    current.id,
                    expected_state_version=current.state_version,
                    status=SubagentExecutionStatus.failed,
                    phase="terminal",
                    error={"category": "child_runtime_error", "message": str(exc)[:500]},
                )
                await session.commit()

    async def reconcile(self, *, parent_state_version: int) -> list[dict[str, Any]]:
        """Consume newly terminal Joins once and return sanitized root observations."""
        observations: list[dict[str, Any]] = []
        joins = SubagentJoinService(self.session)
        run = await RunUnitOfWork(self.session).require_run_core(self.run_id)
        persisted_join_ids = set(
            filter(
                None,
                map(
                    lambda item: (
                        str(item.get("data", {}).get("join_id"))
                        if item.get("kind") == "subagent_join" and item.get("data", {}).get("join_id")
                        else None
                    ),
                    (run.agent_state or {}).get("observations", []),
                ),
            )
        )
        # A process may stop after the Join/event commit but before the in-memory
        # observation is checkpointed into AgentState. Re-project the durable result
        # once on recovery; canonical AgentState and the live guard suppress repeats.
        for consumed in await joins.consumed_for_parent(self.parent_execution_id):
            if any((consumed.id in persisted_join_ids, consumed.id in self._reported_consumed_joins)):
                continue
            observations.append(self._consumed_observation(consumed.result))
            self._reported_consumed_joins.add(consumed.id)
        for join in await joins.ready_for_parent(self.parent_execution_id):
            evaluation = await joins.evaluate(join.id)
            join = await self.session.get(type(join), join.id)
            if join is None:
                continue
            await self.session.refresh(join)
            if evaluation.status == "blocked":
                if join.id in self._reported_blocked_joins:
                    continue
                self._reported_blocked_joins.add(join.id)
                observations.append(
                    {
                        "kind": "subagent_join",
                        "status": "blocked",
                        "summary": "A required subagent Join could not be satisfied.",
                        "data": {
                            "group_id": join.group_id,
                            "join_id": join.id,
                            "failed_execution_ids": list(evaluation.failed_ids),
                        },
                    }
                )
                continue
            if any((evaluation.status != "ready", join.status != "ready")):
                continue
            if evaluation.loser_ids:
                _, unsafe = await joins.cancel_safe_first_success_losers(evaluation)
            else:
                unsafe = ()
            join = await joins.begin_merge(join.id, expected_version=join.state_version)
            validated = await asyncio.gather(*map(joins.validator.validate, evaluation.successful_ids))
            merged = merge_subagent_results(validated)
            payload = {
                **deepcopy(merged.__dict__),
                "group_id": join.group_id,
                "join_id": join.id,
                "unsafe_loser_execution_ids": list(unsafe),
            }
            payload = dict(
                map(lambda item: (item[0], list(item[1]) if isinstance(item[1], tuple) else item[1]), payload.items())
            )
            await joins.mark_consumed(
                join.id,
                expected_version=join.state_version,
                parent_state_version=parent_state_version,
                result=payload,
            )
            await RunUnitOfWork(self.session).add_event(
                self.run_id,
                "subagent.join.consumed",
                {
                    "group_id": join.group_id,
                    "join_id": join.id,
                    "source_execution_ids": payload["source_execution_ids"],
                    "conflict_count": len(payload["conflicts"]),
                },
                agent_execution_id=self.parent_execution_id,
            )
            await self.session.commit()
            observations.append(self._consumed_observation(payload))
            self._reported_consumed_joins.add(join.id)
        return observations

    @staticmethod
    def _consumed_observation(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "kind": "subagent_join",
            "status": "succeeded",
            "summary": "Subagent results were validated and merged.",
            "data": deepcopy(payload),
        }

    async def has_pending(self) -> bool:
        joins = await SubagentJoinService(self.session).ready_for_parent(self.parent_execution_id)
        return any(join.status != "consumed" for join in joins)

    async def wait(self) -> None:
        task = self._dispatch_task
        if task is not None:
            await task

    async def close(self, *, cancel: bool = False) -> None:
        self._closed = True
        if cancel:
            await self.coordinator.cancel()
        elif self._dispatch_task is not None:
            await self._dispatch_task
