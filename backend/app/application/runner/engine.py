import asyncio
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.agent_runtime.policies.reasoning import (
    build_default_contract,
)
from app.application.agent_runtime.services.loop import AgentLoop
from app.application.planning.service import PlanService, PlanValidationError, canonical_agent_state
from app.application.run_management.recovery import ExecutionRecovery
from app.application.runner.answer_stream import AnswerStreamMixin
from app.application.runner.coordinator import RunCoordinator
from app.application.runner.model_thinking_stream import ModelThinkingEventWriter
from app.application.runner.node_worker import ReadOnlyAgentNodeExecutor
from app.application.runner.plan_preparation import PlanPreparationMixin
from app.application.skills.activation import SkillActivationService
from app.common.core.config import Settings
from app.common.core.errors import run_error_from_exception
from app.common.schemas.agent.planning import (
    PlanGraphSnapshotEvent,
)
from app.common.schemas.agent.run_policy import ReasoningPolicySnapshot, RunExecutionProfile
from app.common.schemas.agent.run_result import FinalAnswer
from app.common.schemas.agent.types import AnswerMode, PlanExecution
from app.common.schemas.models import ModelThinkingSnapshot
from app.domain.agent_profile import (
    AgentProfile,
    AgentProfileConfigurationError,
)
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.models.skills import RunSkillSnapshotRecord
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.model_clients.contracts import (
    ModelConfigurationError,
    ModelOutputError,
    model_http_client_options,
)
from app.infrastructure.model_clients.factory import build_model_client
from app.infrastructure.model_clients.usage_metering import DatabaseUsageRecorder
from app.infrastructure.repositories.plans import PlanRepository, plan_to_view
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import ToolRegistry
from app.infrastructure.tools.registry import build_tool_registry
from app.infrastructure.tools.selection import forbidden_plan_bindings, task_capability_catalog

logger = logging.getLogger("astra.engine")

_SHARED_MODEL_HTTP_CLIENTS: dict[str, httpx.AsyncClient] = {}
_SHARED_TOOL_REGISTRIES: OrderedDict[str, ToolRegistry] = OrderedDict()
MAX_SHARED_TOOL_REGISTRIES = 16


def shared_model_http_client(settings: Settings) -> httpx.AsyncClient | None:
    """Reuse provider connections across Runs in the same server process."""
    if settings.model_provider == "mock":
        return None
    endpoint = settings.model_base_url.rstrip("/")
    client = _SHARED_MODEL_HTTP_CLIENTS.get(endpoint)
    if client is None:
        client = httpx.AsyncClient(**model_http_client_options(settings))
        _SHARED_MODEL_HTTP_CLIENTS[endpoint] = client
    return client


async def close_shared_model_http_clients() -> None:
    clients = list(_SHARED_MODEL_HTTP_CLIENTS.values())
    _SHARED_MODEL_HTTP_CLIENTS.clear()
    _SHARED_TOOL_REGISTRIES.clear()
    for client in clients:
        await client.aclose()


def shared_tool_registry(settings: Settings) -> ToolRegistry:
    """Reuse immutable tool manifests without probing the sandbox for every Run."""
    payload = {
        name: value
        for name, value in settings.model_dump(mode="json").items()
        if not name.startswith("model_")
    }
    key = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    registry = _SHARED_TOOL_REGISTRIES.get(key)
    if registry is not None:
        _SHARED_TOOL_REGISTRIES.move_to_end(key)
        return registry
    registry = build_tool_registry(settings)
    _SHARED_TOOL_REGISTRIES[key] = registry
    if len(_SHARED_TOOL_REGISTRIES) > MAX_SHARED_TOOL_REGISTRIES:
        _SHARED_TOOL_REGISTRIES.popitem(last=False)
    return registry


class RunEngine(PlanPreparationMixin, AnswerStreamMixin):
    def __init__(
        self,
        settings: Settings,
        *,
        model_client=None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.settings = settings
        self.model_client = model_client or build_model_client(
            settings,
            http_client=shared_model_http_client(settings),
        )
        self.tool_registry = tool_registry or shared_tool_registry(settings)
        self._answer_buffers: dict[str, str] = {}
        self._answer_flush_at: dict[str, float] = {}
        self._answer_start_pending: set[str] = set()

    async def run(self, run_id: str) -> None:
        if hasattr(self.model_client, "usage_recorder"):
            self.model_client.usage_recorder = DatabaseUsageRecorder(run_id)
        logger.info(
            "run.engine.start run_id=%s provider=%s model=%s",
            run_id,
            self.settings.model_provider,
            self.settings.model_name,
        )
        async with SessionLocal() as session:
            repo = RunUnitOfWork(session)
            try:
                await self._run_with_repo(repo, run_id)
            except asyncio.CancelledError:
                # Reuse the active repository session while draining the final
                # buffered delta. Opening a second SQLite writer here can race
                # with this still-open transaction and surface as "database
                # unavailable" to the cancellation endpoint.
                await repo.session.rollback()
                await self._flush_cancelled_answer(repo, run_id)
                raise
            except (
                AgentProfileConfigurationError,
                ModelConfigurationError,
                ModelOutputError,
                httpx.RequestError,
            ) as exc:
                logger.exception("run.engine.model_error run_id=%s cause=%s", run_id, str(exc))
                # The failed stage may have an open write transaction.  Clear it
                # before recording the terminal failure so SQLite does not keep
                # the Run (and unrelated scheduler writes) locked indefinitely.
                await repo.session.rollback()
                error = run_error_from_exception(exc)
                await repo.add_event(run_id, "run.error", error)
                await repo.update_run_status(
                    run_id, "blocked", summary=error["message"], result=error_result(error)
                )
                await repo.session.commit()
            except Exception as exc:
                logger.exception("run.engine.failed run_id=%s cause=%s", run_id, type(exc).__name__)
                await repo.session.rollback()
                error = run_error_from_exception(exc)
                await repo.add_event(run_id, "run.error", error)
                await repo.update_run_status(
                    run_id,
                    "failed",
                    summary=error["message"],
                    result={
                        "summary": error["message"],
                        "findings": [],
                        "sources": [],
                        "caveats": [],
                        "verification_notes": ["运行未能完成。"],
                        "error": error,
                    },
                )
                await repo.session.commit()

    async def _flush_cancelled_answer(self, repo: RunUnitOfWork, run_id: str) -> None:
        buffered = self._answer_buffers.pop(run_id, "")
        self._answer_flush_at.pop(run_id, None)
        if not buffered:
            self._answer_start_pending.discard(run_id)
            return
        await self._ensure_answer_stream_started(repo, run_id)
        await repo.add_event(run_id, "answer.delta", {"delta": buffered})
        await repo.session.commit()

    async def _run_with_repo(self, repo: RunUnitOfWork, run_id: str) -> None:
        run, skill_snapshot = await repo.require_run_startup(
            run_id,
            include_skills=self.settings.skills_enabled,
        )
        profile = await self._profile_for_run(repo, run_id, run.agent_profile_snapshot or {})
        self.model_client.bind_agent_profile(profile)
        self._bind_reasoning_effort(run)
        self._bind_model_thinking(repo, run)
        skill_snapshot = await self._bind_skills(repo, run, skill_snapshot)
        goal = await self._conversation_goal(repo, run)
        execution_profile = RunExecutionProfile.model_validate(run.execution_profile or {})
        if execution_profile.answer_mode == AnswerMode.standard:
            await self._execute_agent_loop(
                repo,
                run_id,
                goal,
                initial_run=run,
                fresh_run=run.status == "created" and not run.state_version,
                initial_skill_snapshot=skill_snapshot,
            )
            return
        if run.state_version and run.agent_state:
            await repo.session.commit()
            await self._execute_trusted_runtime(repo, run_id, goal)
            return
        if await self._prepare_trusted_run(repo, run, goal, execution_profile):
            return
        await self._execute_trusted_runtime(repo, run_id, goal)

    async def _bind_skills(self, repo, run, skill_snapshot):
        run_id = run.id
        skill_service = SkillActivationService(
            repo.session,
            max_active=self.settings.skills_max_active,
            max_resource_bytes=self.settings.skills_max_resource_bytes_per_run,
        )
        if self.settings.skills_enabled:
            try:
                skill_blocks, skill_snapshot = await skill_service.prompt_blocks_with_snapshot(
                    run_id,
                    snapshot=skill_snapshot,
                )
            except ValueError as exc:
                if "snapshot is unavailable" not in str(exc):
                    raise
                skill_blocks = []
            self.model_client.bind_skills(skill_blocks)
            if skill_blocks:
                await repo.add_event(
                    run_id,
                    "skill.prompt_bound",
                    {
                        "skills": [
                            {
                                "qualified_identity": item["qualified_identity"],
                                "revision_id": item["revision_id"],
                                "digest": item["digest"],
                            }
                            for item in skill_blocks
                        ]
                    },
                )
            execution = RunExecutionProfile.model_validate(run.execution_profile or {})
            if execution.answer_mode == AnswerMode.trusted:
                await repo.add_event(
                    run_id,
                    "skill.resolution.completed",
                    {
                        "selected": [item["qualified_identity"] for item in skill_blocks],
                        "phase": "before_task_contract",
                    },
                )
            self._active_skill_blocks = skill_blocks
        return skill_snapshot

    async def _prepare_trusted_run(self, repo, run, goal, execution_profile) -> bool:
        run_id = run.id
        await self._announce_planning(repo, run_id)
        contract, plan = await self._prepare_plan(
            run_id,
            goal,
            run.reasoning_policy or {},
            run.execution_profile or {},
        )
        logger.info(
            "run.plan.ready run_id=%s nodes=%s capabilities=%s",
            run_id,
            len(plan.nodes),
            len({capability for node in plan.nodes for capability in node.required_capabilities}),
        )
        run = await repo.require_run_core(run_id)
        snapshot = ReasoningPolicySnapshot.model_validate(run.reasoning_policy or {})
        contract = contract or build_default_contract(goal)
        tool_specs = self.tool_registry.specs()
        capabilities = task_capability_catalog(tool_specs)
        forbidden_capabilities = forbidden_plan_bindings(tool_specs)
        plan_service = PlanService(PlanRepository(repo.session))
        try:
            canonical_plan = await plan_service.create(
                run_id,
                plan,
                contract=contract,
                capabilities=capabilities,
                forbidden_capabilities=forbidden_capabilities,
                budgets=snapshot.effective.budgets,
                activate=execution_profile.plan_execution == PlanExecution.auto,
            )
        except PlanValidationError as exc:
            logger.warning(
                "run.plan.validation_fallback run_id=%s reason=%s",
                run_id,
                str(exc),
            )
            plan = self._default_plan(
                "生成回复",
                "在当前可用能力范围内回应用户请求",
                contract=contract,
            )
            canonical_plan = await plan_service.create(
                run_id,
                plan,
                contract=contract,
                capabilities=capabilities,
                forbidden_capabilities=forbidden_capabilities,
                budgets=snapshot.effective.budgets,
                activate=execution_profile.plan_execution == PlanExecution.auto,
            )
        await repo.session.commit()
        await self._emit_skill_plan_binding(repo, run_id, canonical_plan)
        state = canonical_agent_state(contract, canonical_plan, policy_version=snapshot.version)
        if execution_profile.plan_execution == PlanExecution.confirm:
            state = state.model_copy(update={"active_plan_id": None, "active_executions": []})
        if not run.state_version:
            await repo.initialize_reasoning_state(
                run_id,
                task_contract=contract.model_dump(mode="json"),
                plan_graph=plan_to_view(canonical_plan).model_dump(mode="json"),
                agent_state=state.model_dump(mode="json"),
            )
            await repo.add_event(
                run_id,
                "plan.graph.snapshot",
                PlanGraphSnapshotEvent(
                    plan_id=canonical_plan.id,
                    plan_version=canonical_plan.version,
                    graph=plan_to_view(canonical_plan),
                ).model_dump(mode="json"),
            )
            await repo.session.commit()
        if execution_profile.plan_execution == PlanExecution.confirm:
            await repo.set_waiting_state(
                run_id,
                {
                    "kind": "plan_confirmation",
                    "plan_id": canonical_plan.id,
                    "plan_version": canonical_plan.version,
                    "state_version": state.version,
                    "request": "计划已生成，确认后执行。",
                },
            )
            return True
        if contract.ambiguity_status != "clear":
            await repo.set_waiting_state(
                run_id,
                {
                    "paused_node": "build_contract",
                    "state_version": state.version,
                    "plan_version": canonical_plan.version,
                    "request": contract.clarification_question,
                },
            )
            return True
        return False

    @staticmethod
    async def _announce_planning(repo, run_id) -> None:
        logger.info("run.phase run_id=%s phase=planning", run_id)
        await repo.add_event(
            run_id,
            "reasoning.phase.started",
            {"phase": "planning", "label": "正在理解任务并制定计划"},
        )
        await repo.update_run_status(run_id, "planning")

    @staticmethod
    async def _emit_skill_plan_binding(repo, run_id, plan) -> None:
        nodes = [
            {
                "plan_node_id": node.id,
                "node_key": node.node_key,
                "required_skill_ids": list(node.required_skill_ids or []),
            }
            for node in plan.nodes
            if node.required_skill_ids
        ]
        if nodes:
            await repo.add_event(
                run_id,
                "skill.plan_bound",
                {"plan_id": plan.id, "plan_version": plan.version, "nodes": nodes},
            )

    async def _execute_trusted_runtime(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        goal: str,
    ) -> None:
        if self.settings.agent_parallel_execution_enabled:
            executor = ReadOnlyAgentNodeExecutor(
                self.settings,
                model_client=self.model_client,
                tool_registry=self.tool_registry,
            )
            session_factory = async_sessionmaker(
                repo.session.bind,
                expire_on_commit=False,
                class_=type(repo.session),
            )
            recovery = await ExecutionRecovery(
                session_factory,
                stale_seconds=self.settings.agent_execution_stale_seconds,
            ).scan(run_id)
            coordinator = RunCoordinator(
                session_factory,
                server_max_parallel_nodes=self.settings.agent_max_parallel_nodes,
                parallel_execution_enabled=True,
                heartbeat_seconds=self.settings.agent_execution_heartbeat_seconds,
                provider_concurrency_limit=self.settings.agent_provider_concurrency_limit,
                capability_concurrency_limit=self.settings.agent_capability_concurrency_limit,
                parallel_safe_capabilities=executor.safe_capabilities,
                attempt_timeout_seconds=self.settings.agent_node_attempt_timeout_seconds,
                max_safe_retries=self.settings.agent_node_max_safe_retries,
            )
            started = time.monotonic()
            result = await coordinator.run(run_id, executor)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            parallel_run = await repo.require_run(run_id)
            resource_conflict_count = sum(
                execution.wait_reason == "resource_conflict"
                for execution in parallel_run.node_executions
            )
            await repo.add_event(
                run_id,
                "plan.parallel_execution.completed",
                {
                    "requested_concurrency": self.settings.agent_max_parallel_nodes,
                    "achieved_concurrency": result.peak_concurrency,
                    "completed_execution_count": len(result.completed_execution_ids),
                    "failed_execution_count": len(result.failed_execution_ids),
                    "recovered_execution_count": (
                        len(recovery.resumable_execution_ids)
                        + len(recovery.replayable_execution_ids)
                        + len(recovery.unknown_execution_ids)
                    ),
                    "elapsed_ms": elapsed_ms,
                    "queue_wait_ms": 0,
                    "resource_conflict_count": resource_conflict_count,
                },
            )
            await repo.session.commit()
        await self._execute_agent_loop(repo, run_id, goal)

    def _bind_reasoning_effort(self, run: RunRecord) -> None:
        policy = run.reasoning_policy or {}
        effective = policy.get("effective") if isinstance(policy.get("effective"), dict) else {}
        requested = policy.get("requested") if isinstance(policy.get("requested"), dict) else {}
        effort = (
            effective.get("reasoning_effort") or requested.get("reasoning_effort") or "balanced"
        )
        try:
            self.model_client.bind_reasoning_effort(effort)
        except ValueError:
            logger.warning(
                "run.reasoning_effort.invalid run_id=%s effort=%s fallback=balanced",
                run.id,
                effort,
            )
            self.model_client.bind_reasoning_effort("balanced")

    def _bind_model_thinking(self, repo: RunUnitOfWork, run: RunRecord) -> None:
        thinking = (run.model_policy or {}).get("thinking")
        if not isinstance(thinking, dict):
            raise ValueError(f"Run {run.id} is missing the current model thinking snapshot")
        snapshot = ModelThinkingSnapshot.model_validate(thinking)
        self.model_client.bind_model_thinking(snapshot)
        bind_observer = getattr(self.model_client, "bind_model_thinking_observer", None)
        if bind_observer is not None:
            bind_observer(
                ModelThinkingEventWriter(repo, run.id).accept
                if snapshot.effective.enabled
                else None
            )

    async def _execute_agent_loop(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        goal: str,
        *,
        initial_run: RunRecord | None = None,
        fresh_run: bool = False,
        initial_skill_snapshot: RunSkillSnapshotRecord | None = None,
    ) -> None:
        if not self.settings.agent_use_general_runtime:
            raise RuntimeError(
                "The general Agent runtime is required; the legacy Web workflow has been removed"
            )

        run = initial_run or await repo.require_run_core(run_id)
        quick_mode = run.answer_mode == AnswerMode.standard.value
        logger.info("run.phase run_id=%s phase=executing quick=%s", run_id, quick_mode)
        await repo.add_event(
            run_id,
            "reasoning.phase.started",
            {
                "phase": "executing",
                "label": "正在快速回答" if quick_mode else "正在执行计划",
            },
        )
        await repo.update_run_status(
            run_id,
            "executing",
            loaded_run=run if fresh_run else None,
        )
        agent_loop = AgentLoop(
            self.settings,
            model_client=self.model_client,
            tool_registry=self.tool_registry,
        )
        await self._start_answer_stream(repo, run_id)
        try:
            loop_result = await agent_loop.run(
                repo,
                run_id,
                goal,
                on_answer_delta=lambda delta: self._handle_answer_delta(
                    repo,
                    run_id,
                    delta,
                    background_verification=run.answer_mode == AnswerMode.trusted.value,
                ),
                initial_run=run if fresh_run else None,
                fresh_run=fresh_run,
                initial_skill_snapshot=initial_skill_snapshot if fresh_run else None,
            )
        except Exception:
            self._answer_buffers.pop(run_id, None)
            self._answer_flush_at.pop(run_id, None)
            self._answer_start_pending.discard(run_id)
            raise

        final_answer = loop_result["answer"]
        result = loop_result["result"]
        status = loop_result["status"]
        if status == "waiting_user":
            self._answer_buffers.pop(run_id, None)
            self._answer_flush_at.pop(run_id, None)
            await self._ensure_answer_stream_started(repo, run_id)
            await repo.add_event(run_id, "answer.paused", {"status": status})
            await repo.session.commit()
        else:
            await self._complete_answer_stream(repo, run_id, final_answer.summary)
        await self._finalize_agent_loop(repo, run_id, final_answer, result, status)

    async def _finalize_agent_loop(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        final_answer: FinalAnswer,
        result: dict[str, Any],
        status: str,
    ) -> None:
        if status == "waiting_user":
            await repo.update_run_status(run_id, status, summary=final_answer.summary)
            await repo.session.commit()
            logger.info("run.paused run_id=%s status=waiting_user", run_id)
            return
        if result.get("answer_mode") == AnswerMode.standard.value:
            await repo.update_run_status(
                run_id,
                status,
                summary=final_answer.summary,
                result=result,
            )
            await repo.session.commit()
            logger.info(
                "run.complete run_id=%s status=%s mode=standard fast_path=true",
                run_id,
                status,
            )
            return
        await repo.add_event(
            run_id,
            "reasoning.phase.started",
            {"phase": "synthesizing", "label": "正在组织回答"},
        )
        await repo.update_run_status(run_id, "synthesizing")
        synth_step = await self._mark_named_step_running(repo, run_id, "综合")
        await repo.create_artifact(
            run_id,
            "final_answer",
            content_ref=final_answer.model_dump_json(),
            metadata={"format": "json"},
        )
        if synth_step is not None:
            await repo.update_step(
                synth_step.id,
                "completed",
                evidence={
                    "finding_count": len(final_answer.findings),
                    "handled_by": "agent_loop",
                },
            )

        await repo.add_event(
            run_id,
            "reasoning.phase.started",
            {"phase": "verifying", "label": "正在验证结果"},
        )
        await repo.update_run_status(run_id, "verifying")
        verify_step = await self._mark_named_step_running(repo, run_id, "验证")
        if verify_step is not None:
            report = result.get("verification_report", {})
            await repo.update_step(
                verify_step.id,
                "completed",
                evidence={
                    "status": report.get("status", status),
                    "source_count": report.get("source_count", len(result.get("sources", []))),
                    "caveat_count": report.get("caveat_count", len(result.get("caveats", []))),
                },
            )
        current = await repo.require_run_core(run_id)
        if not current.active_plan_id:
            await self._complete_pending_steps(repo, run_id)
        await repo.update_run_status(
            run_id,
            status,
            summary=final_answer.summary,
            result=result,
        )
        await repo.session.commit()
        logger.info(
            "run.complete run_id=%s status=%s findings=%s sources=%s",
            run_id,
            status,
            len(final_answer.findings),
            len(final_answer.sources),
        )

    async def _profile_for_run(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        snapshot: dict[str, Any],
    ) -> AgentProfile:
        return AgentProfile.from_snapshot(snapshot)


async def start_run_in_process(run_id: str, settings: Settings) -> None:
    try:
        engine = RunEngine(settings)
    except ModelConfigurationError as exc:
        logger.exception("run.engine.configuration_error run_id=%s", run_id)
        async with SessionLocal() as session:
            repo = RunUnitOfWork(session)
            error = run_error_from_exception(exc)
            await repo.add_event(run_id, "run.error", error)
            await repo.update_run_status(
                run_id, "blocked", summary=error["message"], result=error_result(error)
            )
        return
    try:
        await engine.run(run_id)
    except asyncio.CancelledError:
        async with SessionLocal() as session:
            await RunUnitOfWork(session).cancel_run(run_id)
        raise
    finally:
        await engine.model_client.aclose()


def error_result(error: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": error["message"],
        "findings": [],
        "sources": [],
        "failed_sources": [],
        "source_quality": [],
        "conflicts": [],
        "caveats": [],
        "verification_notes": ["运行未能完成。"],
        "error": error,
    }
