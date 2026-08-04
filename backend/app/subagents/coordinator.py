from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models.executions import AgentExecutionRecord
from app.repositories.agent_executions import (
    AgentExecutionRepository,
    AgentExecutionStateError,
)

AgentWorker = Callable[[AsyncSession, AgentExecutionRecord, int], Awaitable[Any]]


class HierarchicalSemaphoreRegistry:
    """Process-local enforcement for deployment/Run/provider/tool/capability caps."""

    def __init__(self):
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._capacities: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def _get(self, key: str, capacity: int) -> asyncio.Semaphore:
        capacity = max(1, int(capacity))
        async with self._lock:
            existing = self._semaphores.get(key)
            if existing is not None:
                if self._capacities[key] != capacity:
                    raise ValueError(
                        f"Semaphore capacity changed for {key}: "
                        f"{self._capacities[key]} -> {capacity}"
                    )
                return existing
            semaphore = asyncio.Semaphore(capacity)
            self._semaphores[key] = semaphore
            self._capacities[key] = capacity
            return semaphore

    @asynccontextmanager
    async def acquire(
        self,
        limits: dict[str, int],
    ) -> AsyncIterator[None]:
        acquired: list[asyncio.Semaphore] = []
        try:
            for key, capacity in sorted(limits.items()):
                semaphore = await self._get(key, capacity)
                await semaphore.acquire()
                acquired.append(semaphore)
            yield
        finally:
            for semaphore in reversed(acquired):
                semaphore.release()


@dataclass(frozen=True)
class AgentCoordinatorResult:
    claimed_ids: tuple[str, ...]
    completed_ids: tuple[str, ...]
    failed_ids: tuple[str, ...]
    queued_ids: tuple[str, ...]
    peak_concurrency: int
    dynamic_node_allowance: int


class AgentCoordinator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        deployment_max_agents: int,
        run_max_agents: int,
        run_max_nodes: int,
        heartbeat_seconds: float = 10,
        semaphore_registry: HierarchicalSemaphoreRegistry | None = None,
    ):
        self.session_factory = session_factory
        self.deployment_max_agents = max(1, deployment_max_agents)
        self.run_max_agents = max(1, run_max_agents)
        self.run_max_nodes = max(1, run_max_nodes)
        self.heartbeat_seconds = max(0.05, heartbeat_seconds)
        self.semaphores = semaphore_registry or HierarchicalSemaphoreRegistry()
        self._cancelled = asyncio.Event()
        self._tasks: set[asyncio.Task[Any]] = set()

    async def run_available(
        self,
        run_id: str,
        worker: AgentWorker,
        *,
        provider: str | None = None,
        provider_limit: int | None = None,
        tool_group: str | None = None,
        tool_limit: int | None = None,
        capability: str | None = None,
        capability_limit: int | None = None,
    ) -> AgentCoordinatorResult:
        async with self.session_factory() as session:
            queued = list(
                (
                    await session.scalars(
                        select(AgentExecutionRecord)
                        .where(
                            AgentExecutionRecord.run_id == run_id,
                            AgentExecutionRecord.parent_execution_id.is_not(None),
                            AgentExecutionRecord.status == "queued",
                        )
                        .order_by(
                            AgentExecutionRecord.depth,
                            AgentExecutionRecord.ordinal,
                            AgentExecutionRecord.created_at,
                        )
                    )
                ).all()
            )
        selected = queued[: self.run_max_agents]
        remaining = queued[self.run_max_agents :]
        if not selected or self._cancelled.is_set():
            return AgentCoordinatorResult(
                (), (), (), tuple(item.id for item in queued), 0, self.run_max_nodes
            )
        node_allowance = max(1, self.run_max_nodes // len(selected))
        limits = self._limits(
            run_id, provider, provider_limit, tool_group, tool_limit,
            capability, capability_limit,
        )
        peak = 0
        active = 0
        active_lock = asyncio.Lock()

        async def run_one(execution_id: str) -> tuple[str, bool]:
            nonlocal active, peak
            async with self.semaphores.acquire(limits):
                async with active_lock:
                    active += 1
                    peak = max(peak, active)
                try:
                    return await self._run_one(execution_id, worker, node_allowance)
                finally:
                    async with active_lock:
                        active -= 1

        tasks = {
            asyncio.create_task(run_one(item.id), name=f"subagent-worker:{item.id}")
            for item in selected
        }
        self._tasks.update(tasks)
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.difference_update(tasks)
        completed: list[str] = []
        failed: list[str] = []
        for selected_item, result in zip(selected, results, strict=True):
            if isinstance(result, BaseException):
                failed.append(selected_item.id)
            elif result[1]:
                completed.append(result[0])
            else:
                failed.append(result[0])
        return AgentCoordinatorResult(
            claimed_ids=tuple(item.id for item in selected),
            completed_ids=tuple(completed),
            failed_ids=tuple(failed),
            queued_ids=tuple(item.id for item in remaining),
            peak_concurrency=peak,
            dynamic_node_allowance=node_allowance,
        )

    def _limits(
        self, run_id, provider, provider_limit, tool_group, tool_limit,
        capability, capability_limit,
    ):
        limits = {
            "deployment:agents": self.deployment_max_agents,
            f"run:{run_id}:agents": self.run_max_agents,
        }
        optional = (
            (f"provider:{provider}", provider_limit, provider),
            (f"tool:{tool_group}", tool_limit, tool_group),
            (f"capability:{capability}", capability_limit, capability),
        )
        limits.update({key: limit for key, limit, name in optional if name and limit})
        return limits

    async def _run_one(
        self,
        execution_id: str,
        worker: AgentWorker,
        node_allowance: int,
    ) -> tuple[str, bool]:
        worker_id = f"agent-worker-{uuid.uuid4()}"
        async with self.session_factory() as session:
            repository = AgentExecutionRepository(session)
            execution = await repository.require(execution_id)
            execution = await repository.claim(
                execution.id,
                worker_id=worker_id,
                expected_state_version=execution.state_version,
                expected_cancellation_epoch=execution.cancellation_epoch,
            )
            execution.checkpoint = {
                **(execution.checkpoint or {}),
                "dynamic_node_allowance": node_allowance,
            }
            await session.commit()
            fencing_token = execution.fencing_token
            cancellation_epoch = execution.cancellation_epoch
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(
                execution_id,
                worker_id,
                fencing_token,
                cancellation_epoch,
                stop,
            ),
            name=f"subagent-heartbeat:{execution_id}",
        )
        try:
            async with self.session_factory() as worker_session:
                execution = await AgentExecutionRepository(worker_session).require(execution_id)
                await worker(worker_session, execution, fencing_token)
                await worker_session.commit()
        except asyncio.CancelledError:
            raise
        finally:
            stop.set()
            await heartbeat
        async with self.session_factory() as session:
            execution = await AgentExecutionRepository(session).require(execution_id)
            return execution.id, execution.status in {
                "completed",
                "completed_with_warnings",
            }

    async def _heartbeat(
        self,
        execution_id: str,
        worker_id: str,
        fencing_token: int,
        cancellation_epoch: int,
        stop: asyncio.Event,
    ) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_seconds)
                return
            except TimeoutError:
                pass
            async with self.session_factory() as session:
                try:
                    await AgentExecutionRepository(session).heartbeat(
                        execution_id,
                        worker_id=worker_id,
                        fencing_token=fencing_token,
                        cancellation_epoch=cancellation_epoch,
                    )
                    await session.commit()
                except (ValueError, AgentExecutionStateError):
                    await session.rollback()
                    return

    async def cancel(self) -> None:
        self._cancelled.set()
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
