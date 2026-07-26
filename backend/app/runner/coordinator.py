from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import NodeExecutionRecord, PlanNodeRecord, RunRecord, utc_now
from app.repositories.executions import (
    NodeExecutionRepository,
    NodeExecutionStateError,
)
from app.repositories.plans import PlanRepository
from app.repositories.runs import RunRepository
from app.runner.planning import PlanScheduler
from app.schemas.agent import (
    NodeExecutionPhase,
    NodeExecutionStatus,
    PlanNodeStatus,
)


class NodeContextSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    execution_id: str
    plan_id: str
    plan_version: int
    plan_node_id: str
    attempt: int
    node: dict[str, Any]
    dependency_evidence: tuple[str, ...] = ()
    task_contract: dict[str, Any] = Field(default_factory=dict)
    policy: dict[str, Any] = Field(default_factory=dict)
    accepted_facts: tuple[dict[str, Any], ...] = ()
    reserved_budgets: dict[str, int] = Field(default_factory=dict)
    retry_safe: bool = False
    state_version: int


class NodeExecutionResult(BaseModel):
    execution_id: str
    plan_node_id: str
    plan_version: int
    attempt: int
    status: NodeExecutionStatus = NodeExecutionStatus.completed
    evidence_refs: list[str] = Field(default_factory=list)
    accepted_facts: list[dict[str, Any]] = Field(default_factory=list)
    criterion_updates: dict[str, str] = Field(default_factory=dict)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    budget_consumed: dict[str, int] = Field(default_factory=dict)
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    failure: dict[str, Any] | None = None
    retryable: bool = False


NodeExecutor = Callable[
    [RunRepository, NodeContextSnapshot],
    Awaitable[NodeExecutionResult],
]


@dataclass(frozen=True)
class CoordinatorResult:
    completed_execution_ids: tuple[str, ...]
    failed_execution_ids: tuple[str, ...]
    peak_concurrency: int


class NodeWorker:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        executor: NodeExecutor,
        *,
        heartbeat_seconds: float = 10,
        attempt_timeout_seconds: float = 120,
    ):
        self.session_factory = session_factory
        self.executor = executor
        self.heartbeat_seconds = max(0.05, heartbeat_seconds)
        self.attempt_timeout_seconds = max(0.05, attempt_timeout_seconds)

    async def run(
        self,
        execution_id: str,
        context: NodeContextSnapshot,
    ) -> NodeExecutionResult:
        worker_id = f"worker-{uuid.uuid4()}"
        async with self.session_factory() as session:
            executions = NodeExecutionRepository(session)
            execution = await executions.require(execution_id)
            execution.worker_id = worker_id
            execution = await executions.transition(
                execution.id,
                expected_version=execution.state_version,
                phase=NodeExecutionPhase.running,
            )
            await RunRepository(session).add_event(
                execution.run_id,
                "plan.node.execution_started",
                _execution_event(execution),
            )
            await session.commit()

        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._heartbeat(execution_id, stop_heartbeat),
            name=f"node-heartbeat:{execution_id}",
        )
        try:
            async with self.session_factory() as worker_session:
                result = await asyncio.wait_for(
                    self.executor(
                        RunRepository(worker_session),
                        context,
                    ),
                    timeout=self.attempt_timeout_seconds,
                )
                if result.execution_id != execution_id:
                    raise ValueError("Worker returned a result for another execution")
                await worker_session.commit()
        except asyncio.CancelledError:
            result = NodeExecutionResult(
                execution_id=execution_id,
                plan_node_id=context.plan_node_id,
                plan_version=context.plan_version,
                attempt=context.attempt,
                status=NodeExecutionStatus.cancelled,
                failure={"category": "cancelled"},
            )
            raise
        except TimeoutError:
            result = NodeExecutionResult(
                execution_id=execution_id,
                plan_node_id=context.plan_node_id,
                plan_version=context.plan_version,
                attempt=context.attempt,
                status=NodeExecutionStatus.failed,
                failure={
                    "category": "attempt_timeout",
                    "timeout_seconds": self.attempt_timeout_seconds,
                },
                retryable=context.retry_safe,
            )
        except Exception as exc:
            result = NodeExecutionResult(
                execution_id=execution_id,
                plan_node_id=context.plan_node_id,
                plan_version=context.plan_version,
                attempt=context.attempt,
                status=NodeExecutionStatus.failed,
                failure={
                    "category": "worker_error",
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        finally:
            stop_heartbeat.set()
            await heartbeat

        if result.status == NodeExecutionStatus.waiting:
            return result

        async with self.session_factory() as session:
            executions = NodeExecutionRepository(session)
            execution = await executions.require(execution_id)
            if execution.status in {
                NodeExecutionStatus.active.value,
                NodeExecutionStatus.waiting.value,
            }:
                execution = await executions.transition(
                    execution.id,
                    expected_version=execution.state_version,
                    phase=NodeExecutionPhase.committing,
                    checkpoint=result.checkpoint,
                    result=result.model_dump(mode="json"),
                    failure=result.failure,
                )
                await RunRepository(session).add_event(
                    execution.run_id,
                    "plan.node.execution_result_recorded",
                    _execution_event(execution),
                )
                await session.commit()
        return result

    async def _heartbeat(self, execution_id: str, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_seconds)
                return
            except TimeoutError:
                pass
            async with self.session_factory() as session:
                executions = NodeExecutionRepository(session)
                try:
                    execution = await executions.require(execution_id)
                    await executions.heartbeat(
                        execution_id,
                        expected_version=execution.state_version,
                    )
                    await executions.renew_leases(
                        execution_id,
                        ttl_seconds=max(1, int(self.heartbeat_seconds * 3)),
                    )
                    await session.commit()
                except (ValueError, NodeExecutionStateError):
                    await session.rollback()
                    return


class RunCoordinator:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        server_max_parallel_nodes: int = 3,
        parallel_execution_enabled: bool = True,
        heartbeat_seconds: float = 10,
        provider_concurrency_limit: int = 8,
        capability_concurrency_limit: int = 4,
        parallel_safe_capabilities: set[str] | None = None,
        attempt_timeout_seconds: float = 120,
        max_safe_retries: int = 1,
    ):
        self.session_factory = session_factory
        self.server_max_parallel_nodes = max(1, server_max_parallel_nodes)
        self.parallel_execution_enabled = parallel_execution_enabled
        self.heartbeat_seconds = heartbeat_seconds
        self.provider_concurrency_limit = provider_concurrency_limit
        self.capability_concurrency_limit = capability_concurrency_limit
        self.parallel_safe_capabilities = parallel_safe_capabilities
        self.attempt_timeout_seconds = max(0.05, attempt_timeout_seconds)
        self.max_safe_retries = max(0, max_safe_retries)
        self._cancelled = asyncio.Event()
        self._workers: set[asyncio.Task[NodeExecutionResult]] = set()

    async def run(self, run_id: str, executor: NodeExecutor) -> CoordinatorResult:
        completed: list[str] = []
        failed: list[str] = []
        peak = 0
        replayed = await self._replay_recorded_results(run_id)
        completed.extend(replayed[0])
        failed.extend(replayed[1])
        while not self._cancelled.is_set():
            contexts = await self._claim_contexts(run_id)
            if contexts:
                worker = NodeWorker(
                    self.session_factory,
                    executor,
                    heartbeat_seconds=self.heartbeat_seconds,
                    attempt_timeout_seconds=self.attempt_timeout_seconds,
                )
                tasks = {
                    asyncio.create_task(
                        worker.run(context.execution_id, context),
                        name=f"node-worker:{context.execution_id}",
                    ): context
                    for context in contexts
                }
                self._workers.update(tasks)
                peak = max(peak, len(tasks))
                results = await asyncio.gather(*tasks, return_exceptions=True)
                self._workers.difference_update(tasks)
                if self._cancelled.is_set():
                    break
                for context, result in zip(contexts, results, strict=True):
                    if isinstance(result, BaseException):
                        node_result = NodeExecutionResult(
                            execution_id=context.execution_id,
                            plan_node_id=context.plan_node_id,
                            plan_version=context.plan_version,
                            attempt=context.attempt,
                            status=NodeExecutionStatus.failed,
                            failure={
                                "category": "worker_crashed",
                                "type": type(result).__name__,
                                "message": str(result),
                            },
                        )
                    else:
                        node_result = result
                    if node_result.status == NodeExecutionStatus.waiting:
                        continue
                    retried = await self._merge_result(run_id, node_result)
                    if retried:
                        continue
                    if node_result.status == NodeExecutionStatus.completed:
                        completed.append(node_result.execution_id)
                    else:
                        failed.append(node_result.execution_id)
                continue
            # Workers in a batch are awaited above, so there is no in-process
            # work that can make a waiting execution dispatchable here.  Leave
            # approval, resource-wait and recovery states to their dedicated
            # resumptions instead of spinning the coordinator indefinitely.
            break
        if self._cancelled.is_set():
            await self._persist_cancellation(run_id)
        return CoordinatorResult(tuple(completed), tuple(failed), peak)

    async def cancel(self) -> None:
        self._cancelled.set()
        for task in tuple(self._workers):
            task.cancel()
        if self._workers:
            await asyncio.gather(*self._workers, return_exceptions=True)

    async def _claim_contexts(self, run_id: str) -> list[NodeContextSnapshot]:
        async with self.session_factory() as session:
            run_repository = RunRepository(session)
            run = await run_repository.require_run(run_id)
            if run.status in {"cancelled", "draining_for_replan"}:
                return []
            recovered_result = await session.execute(
                select(NodeExecutionRecord)
                .where(
                    NodeExecutionRecord.run_id == run_id,
                    NodeExecutionRecord.current_slot == "current",
                    NodeExecutionRecord.phase == NodeExecutionPhase.claimed.value,
                    NodeExecutionRecord.status == NodeExecutionStatus.active.value,
                    NodeExecutionRecord.worker_id.is_(None),
                )
                .order_by(
                    NodeExecutionRecord.started_at,
                    NodeExecutionRecord.id,
                )
                .limit(self.server_max_parallel_nodes)
            )
            recovered = list(recovered_result.scalars().all())
            if recovered:
                for execution in recovered:
                    if execution.slot_index is None:
                        await NodeExecutionRepository(session).acquire_slot(
                            execution.id,
                            expected_version=execution.state_version,
                            total_slots=self.server_max_parallel_nodes,
                        )
                        execution = await NodeExecutionRepository(session).transition(
                            execution.id,
                            expected_version=execution.state_version + 1,
                            phase=NodeExecutionPhase.claimed,
                        )
                contexts = [
                    await _build_context(
                        session,
                        run,
                        execution,
                        parallel_safe_capabilities=self.parallel_safe_capabilities,
                    )
                    for execution in recovered
                ]
                await session.commit()
                return contexts
            scheduler = PlanScheduler(
                PlanRepository(session),
                server_max_parallel_nodes=self.server_max_parallel_nodes,
                parallel_execution_enabled=self.parallel_execution_enabled,
                provider_concurrency_limit=self.provider_concurrency_limit,
                capability_concurrency_limit=self.capability_concurrency_limit,
                parallel_safe_capabilities=self.parallel_safe_capabilities,
            )
            batch = await scheduler.claim_ready_batch(run_id)
            if batch is None:
                await session.commit()
                return []
            contexts = [
                await _build_context(
                    session,
                    run,
                    execution,
                    parallel_safe_capabilities=self.parallel_safe_capabilities,
                )
                for execution in batch.executions
            ]
            await session.commit()
            return contexts

    async def _replay_recorded_results(
        self,
        run_id: str,
    ) -> tuple[list[str], list[str]]:
        async with self.session_factory() as session:
            result = await session.execute(
                select(NodeExecutionRecord).where(
                    NodeExecutionRecord.run_id == run_id,
                    NodeExecutionRecord.current_slot == "current",
                    NodeExecutionRecord.phase == NodeExecutionPhase.committing.value,
                    NodeExecutionRecord.result.is_not(None),
                )
            )
            recorded = [
                NodeExecutionResult.model_validate(execution.result)
                for execution in result.scalars().all()
            ]
        completed: list[str] = []
        failed: list[str] = []
        for item in recorded:
            retried = await self._merge_result(run_id, item)
            if retried:
                continue
            (
                completed
                if item.status == NodeExecutionStatus.completed
                else failed
            ).append(item.execution_id)
        return completed, failed

    async def _merge_result(
        self,
        run_id: str,
        result: NodeExecutionResult,
    ) -> bool:
        async with self.session_factory() as session:
            execution_repository = NodeExecutionRepository(session)
            execution = await execution_repository.require(result.execution_id)
            if (
                execution.run_id != run_id
                or execution.plan_version != result.plan_version
                or execution.plan_node_id != result.plan_node_id
                or execution.attempt != result.attempt
                or execution.current_slot != "current"
                or execution.phase != NodeExecutionPhase.committing.value
            ):
                raise NodeExecutionStateError("Stale NodeExecution result")
            node = await session.get(PlanNodeRecord, result.plan_node_id)
            if node is None or node.status != PlanNodeStatus.running.value:
                raise NodeExecutionStateError("Plan node is not owned by the current execution")
            run = await session.get(RunRecord, run_id)
            if run is None or run.active_plan_id != execution.plan_id:
                raise NodeExecutionStateError("Run Plan changed before result commit")

            should_retry = (
                result.status == NodeExecutionStatus.failed
                and result.retryable
                and result.attempt <= self.max_safe_retries
            )
            if should_retry:
                await execution_repository.settle_budgets(
                    execution.id,
                    consumed=result.budget_consumed,
                    status="settled",
                )
                await execution_repository.release_leases(
                    execution.id,
                    reason="retry",
                )
                execution = await execution_repository.transition(
                    execution.id,
                    expected_version=execution.state_version,
                    phase=NodeExecutionPhase.failed,
                    status=NodeExecutionStatus.failed,
                    checkpoint=result.checkpoint,
                    result=result.model_dump(mode="json"),
                    failure=result.failure,
                )
                node.status = PlanNodeStatus.pending.value
                node.started_at = None
                node.completed_at = None
                node.failure = None
                _merge_run_state(run, result)
                await RunRepository(session).add_event(
                    run_id,
                    "plan.node.execution_retry_scheduled",
                    {
                        **_execution_event(execution),
                        "next_attempt": result.attempt + 1,
                        "reason": (result.failure or {}).get("category", "retryable_failure"),
                    },
                )
                await session.commit()
                return True

            terminal_phase = (
                NodeExecutionPhase.completed
                if result.status == NodeExecutionStatus.completed
                else NodeExecutionPhase.cancelled
                if result.status == NodeExecutionStatus.cancelled
                else NodeExecutionPhase.failed
            )
            target_node_status = (
                PlanNodeStatus.completed.value
                if result.status == NodeExecutionStatus.completed
                else PlanNodeStatus.blocked.value
                if result.status in {NodeExecutionStatus.cancelled, NodeExecutionStatus.blocked}
                else PlanNodeStatus.failed.value
            )
            changed = await session.execute(
                update(PlanNodeRecord)
                .where(
                    PlanNodeRecord.id == node.id,
                    PlanNodeRecord.status == PlanNodeStatus.running.value,
                )
                .values(
                    status=target_node_status,
                    evidence_refs=list(dict.fromkeys(result.evidence_refs)),
                    failure=result.failure,
                    completed_at=utc_now(),
                )
            )
            if changed.rowcount != 1:
                raise NodeExecutionStateError("Plan node changed before result commit")
            await execution_repository.settle_budgets(
                execution.id,
                consumed=result.budget_consumed,
                status="settled",
            )
            await execution_repository.release_leases(
                execution.id,
                reason=result.status.value,
            )
            execution = await execution_repository.transition(
                execution.id,
                expected_version=execution.state_version,
                phase=terminal_phase,
                status=result.status,
                checkpoint=result.checkpoint,
                result=result.model_dump(mode="json"),
                failure=result.failure,
            )
            _merge_run_state(run, result)
            await RunRepository(session).add_event(
                run_id,
                f"plan.node.execution_{result.status.value}",
                _execution_event(execution),
            )
            await session.commit()
            return False

    async def _persist_cancellation(self, run_id: str) -> None:
        async with self.session_factory() as session:
            await RunRepository(session).cancel_run(run_id)


async def _build_context(
    session: AsyncSession,
    run: RunRecord,
    execution: NodeExecutionRecord,
    *,
    parallel_safe_capabilities: set[str] | None = None,
) -> NodeContextSnapshot:
    node = await session.get(PlanNodeRecord, execution.plan_node_id)
    if node is None:
        raise ValueError(f"Plan node not found: {execution.plan_node_id}")
    dependencies = (
        await PlanRepository(session).require(execution.plan_id)
    ).edges
    predecessor_ids = {
        edge.predecessor_id
        for edge in dependencies
        if edge.successor_id == execution.plan_node_id
    }
    evidence_result = await session.execute(
        select(PlanNodeRecord.evidence_refs).where(PlanNodeRecord.id.in_(predecessor_ids))
    )
    evidence = tuple(
        dict.fromkeys(
            ref
            for refs in evidence_result.scalars().all()
            for ref in (refs or [])
        )
    )
    state = run.agent_state or {}
    execution = await NodeExecutionRepository(session).require(execution.id)
    return NodeContextSnapshot(
        run_id=run.id,
        execution_id=execution.id,
        plan_id=execution.plan_id,
        plan_version=execution.plan_version,
        plan_node_id=execution.plan_node_id,
        attempt=execution.attempt,
        node={
            "id": node.id,
            "node_key": node.node_key,
            "index": node.index,
            "title": node.title,
            "intent": node.intent,
            "required_capabilities": list(node.required_capabilities or []),
            "success_criteria_refs": list(node.success_criteria_refs or []),
            "expected_outcome": dict(node.expected_outcome or {}),
            "risk_level": node.risk_level,
            "optional": node.optional,
        },
        dependency_evidence=evidence,
        task_contract=dict(run.task_contract or {}),
        policy=dict(run.reasoning_policy or {}),
        accepted_facts=tuple(state.get("accepted_facts", [])),
        reserved_budgets={
            reservation.budget_kind: reservation.reserved
            for reservation in execution.budget_reservations
        },
        retry_safe=not node.required_capabilities
        or (
            parallel_safe_capabilities is not None
            and set(node.required_capabilities) <= parallel_safe_capabilities
        ),
        state_version=run.state_version,
    )


def _merge_run_state(run: RunRecord, result: NodeExecutionResult) -> None:
    state = dict(run.agent_state or {})
    state["active_executions"] = [
        item
        for item in state.get("active_executions", [])
        if item.get("execution_id") != result.execution_id
    ]
    facts = list(state.get("accepted_facts", []))
    by_id = {item.get("id"): item for item in facts if item.get("id")}
    evaluations = list(state.get("evaluations", []))
    for fact in result.accepted_facts:
        existing = by_id.get(fact.get("id"))
        if existing is not None and existing.get("statement") != fact.get("statement"):
            evaluations.append(
                {
                    "plan_node_id": result.plan_node_id,
                    "node_execution_id": result.execution_id,
                    "outcome": "conflict",
                    "summary": "Concurrent fact conflict requires resolution.",
                    "conflicts": [{"accepted": existing, "proposed": fact}],
                }
            )
            continue
        if existing is None:
            facts.append(fact)
            by_id[fact.get("id")] = fact
    state["accepted_facts"] = facts
    state["evaluations"] = evaluations
    state["observations"] = [*state.get("observations", []), *result.observations]
    usage = dict(state.get("budget_usage", {}))
    for kind, consumed in result.budget_consumed.items():
        usage[kind] = int(usage.get(kind, 0)) + int(consumed)
    state["budget_usage"] = usage
    state["version"] = int(state.get("version", run.state_version)) + 1
    run.agent_state = state
    run.state_version = state["version"]
    run.updated_at = utc_now()


def _execution_event(execution: NodeExecutionRecord) -> dict[str, Any]:
    return {
        "node_execution_id": execution.id,
        "plan_id": execution.plan_id,
        "plan_version": execution.plan_version,
        "plan_node_id": execution.plan_node_id,
        "attempt": execution.attempt,
        "dispatch_batch_id": execution.dispatch_batch_id,
        "phase": execution.phase,
        "status": execution.status,
        "state_version": execution.state_version,
        "started_at": execution.started_at.isoformat(),
        "heartbeat_at": execution.heartbeat_at.isoformat(),
        "finished_at": execution.finished_at.isoformat()
        if execution.finished_at
        else None,
    }
