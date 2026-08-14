import asyncio
import logging
import time
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.application.agent_runtime.policies.reasoning import (
    build_default_contract,
)
from app.application.planning.coordinator import RunCoordinator
from app.application.planning.preparation import PlanPreparation
from app.application.planning.service import PlanService, PlanValidationError, canonical_agent_state
from app.application.run_management.execution.recovery import scan_run_recovery
from app.application.run_management.lifecycle.answer_stream import AnswerStream
from app.application.run_management.lifecycle.finalization import (
    finalize_standard_run,
    finalize_trusted_run,
)
from app.application.run_management.lifecycle.model_thinking_stream import (
    ModelThinkingEventWriter,
)
from app.application.run_management.lifecycle.runtime_events import RunRuntimeEventPort
from app.application.skills.activation import SkillActivationService
from app.common.core.config import AstraRuntimeSettings
from app.common.core.errors import run_error_from_exception
from app.common.schemas.agent.planning import (
    PlanGraphSnapshotEvent,
)
from app.common.schemas.agent.run_policy import ReasoningPolicySnapshot, RunExecutionProfile
from app.common.schemas.agent.types import AnswerMode, PlanExecution, RuntimeKind
from app.common.schemas.model_providers import ModelThinkingSnapshot
from app.domain.agent_profile import (
    AgentProfile,
    AgentProfileConfigurationError,
)
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.model_clients.contracts import (
    ModelConfigurationError,
    ModelOutputError,
)
from app.infrastructure.model_clients.factory import build_model_client
from app.infrastructure.model_clients.usage_metering import DatabaseUsageRecorder
from app.infrastructure.repositories.plans import PlanRepository, plan_to_view
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.runtime.dependencies import (
    shared_model_http_client,
    shared_tool_registry,
)
from app.infrastructure.runtime.node import build_node_executor
from app.infrastructure.runtime.standard import (
    run_standard_runtime,
    standard_compatible_skills,
)
from app.infrastructure.runtime.trusted import run_trusted_runtime
from app.infrastructure.tools.base import AstraToolRegistry
from app.infrastructure.tools.router import ToolRouter
from app.infrastructure.tools.selection import forbidden_plan_bindings, task_capability_catalog

logger = logging.getLogger("astra.engine")


class RunExecution:
    def __init__(
        self,
        settings: AstraRuntimeSettings,
        *,
        model_client=None,
        tool_registry: AstraToolRegistry | None = None,
    ):
        self.settings = settings
        self.model_client = model_client or build_model_client(
            settings,
            http_client=shared_model_http_client(settings),
        )
        self.tool_registry = tool_registry or shared_tool_registry(settings)
        self.plan_preparation = PlanPreparation(settings, self.model_client)
        self.answers = AnswerStream()

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
                await repo.update_run_status(run_id, "blocked", summary=error["message"], result=error_result(error))
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
        buffered = self.answers._answer_buffers.pop(run_id, "")
        self.answers._answer_flush_at.pop(run_id, None)
        if not buffered:
            self.answers._answer_start_pending.discard(run_id)
            return
        await self.answers._ensure_answer_stream_started(repo, run_id)
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
        goal = await self.plan_preparation.conversation_goal(repo, run)
        execution_profile = RunExecutionProfile.model_validate(run.execution_profile or {})
        if execution_profile.runtime_kind == RuntimeKind.fast_v1:
            await self._execute_standard_runtime(repo, run, goal)
            return
        if run.state_version and run.agent_state:
            await repo.session.commit()
            await self._execute_trusted_runtime(repo, run_id, goal)
            return
        if await self._prepare_trusted_run(repo, run, goal, execution_profile):
            return
        await self._execute_trusted_runtime(repo, run_id, goal)

    async def _execute_standard_runtime(
        self,
        repo: RunUnitOfWork,
        run: RunRecord,
        goal: str,
    ) -> None:
        run_id = run.id

        async def persist_terminal(outcome, metrics) -> None:
            if outcome.kind == "waiting":
                await self.answers._ensure_answer_stream_started(repo, run_id)
                await repo.add_event(run_id, "answer.paused", {"status": "waiting_user"})
                await repo.session.commit()
            else:
                await self.answers._complete_answer_stream(repo, run_id, outcome.answer or outcome.reason)
            await finalize_standard_run(repo, run_id, outcome, metrics)

        backends = {"in_process", "astra.runtime"}
        if self.settings.sandbox_enabled:
            backends.add("sandbox.remote")
        await self.answers._start_answer_stream(repo, run_id)
        try:
            await run_standard_runtime(
                settings=self.settings,
                model_client=self.model_client,
                router=ToolRouter(self.tool_registry, available_backends=backends),
                repository=repo,
                run_id=run_id,
                goal=goal,
                active_skills=getattr(self, "_active_skill_blocks", []),
                on_answer_delta=lambda delta: self.answers._handle_answer_delta(
                    repo,
                    run_id,
                    delta,
                    background_verification=False,
                ),
                on_terminal=persist_terminal,
                event_port=RunRuntimeEventPort(repo, run_id, run, self.tool_registry),
            )
        except asyncio.CancelledError:
            self.answers._answer_buffers.pop(run_id, None)
            self.answers._answer_flush_at.pop(run_id, None)
            self.answers._answer_start_pending.discard(run_id)
            await repo.add_event(run_id, "fast.cancelled", {"status": "cancelled"})
            await repo.session.commit()
            raise
        except Exception:
            self.answers._answer_buffers.pop(run_id, None)
            self.answers._answer_flush_at.pop(run_id, None)
            self.answers._answer_start_pending.discard(run_id)
            raise

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
            execution = RunExecutionProfile.model_validate(run.execution_profile or {})
            bound_skill_blocks = (
                standard_compatible_skills(skill_blocks) if execution.runtime_kind == RuntimeKind.fast_v1 else skill_blocks
            )
            self.model_client.bind_skills(bound_skill_blocks)
            if len(bound_skill_blocks) != len(skill_blocks):
                await repo.add_event(
                    run_id,
                    "skill.fast_incompatible",
                    {"excluded": [item["qualified_identity"] for item in skill_blocks if item not in bound_skill_blocks]},
                )
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
            if execution.answer_mode == AnswerMode.trusted:
                await repo.add_event(
                    run_id,
                    "skill.resolution.completed",
                    {
                        "selected": [item["qualified_identity"] for item in skill_blocks],
                        "phase": "before_task_contract",
                    },
                )
            self._active_skill_blocks = bound_skill_blocks
        return skill_snapshot

    async def _prepare_trusted_run(self, repo, run, goal, execution_profile) -> bool:
        run_id = run.id
        await self._announce_planning(repo, run_id)
        contract, plan = await self.plan_preparation.prepare_plan(
            run_id,
            goal,
            run.reasoning_policy or {},
            run.execution_profile or {},
            active_skill_blocks=getattr(self, "_active_skill_blocks", []),
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
            plan = self.plan_preparation.default_plan(
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
            await repo.session.commit()
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
            await repo.session.commit()
            return True
        return False

    @staticmethod
    async def _announce_planning(repo, run_id) -> None:
        logger.info("run.phase run_id=%s phase=planning", run_id)
        await repo.add_event(run_id, "reasoning.phase.started", {"phase": "planning", "label": "正在理解任务并制定计划"})
        await repo.update_run_status(run_id, "planning")
        await repo.session.commit()

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
            executor = build_node_executor(self.settings, self.model_client, self.tool_registry)
            session_factory = async_sessionmaker(
                repo.session.bind,
                expire_on_commit=False,
                class_=type(repo.session),
            )
            recovery = await scan_run_recovery(
                session_factory,
                run_id,
                stale_seconds=self.settings.agent_execution_stale_seconds,
            )
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
                execution.wait_reason == "resource_conflict" for execution in parallel_run.node_executions
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
        effort = effective.get("reasoning_effort") or requested.get("reasoning_effort") or "balanced"
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
            bind_observer(ModelThinkingEventWriter(repo, run.id).accept if snapshot.effective.enabled else None)

    async def _execute_agent_loop(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        goal: str,
    ) -> None:
        run = await repo.require_run_core(run_id)
        background_verification = run.answer_mode == AnswerMode.trusted.value
        logger.info("run.phase run_id=%s phase=executing", run_id)
        await repo.add_event(
            run_id,
            "reasoning.phase.started",
            {
                "phase": "executing",
                "label": "正在执行计划",
            },
        )
        await repo.update_run_status(run_id, "executing")
        await repo.session.commit()
        await self.answers._start_answer_stream(repo, run_id)
        try:
            loop_result = await run_trusted_runtime(
                settings=self.settings,
                model_client=self.model_client,
                tool_registry=self.tool_registry,
                repository=repo,
                run_id=run_id,
                goal=goal,
                on_answer_delta=lambda delta: self.answers._handle_answer_delta(
                    repo,
                    run_id,
                    delta,
                    background_verification=background_verification,
                ),
            )
        except Exception:
            self.answers._answer_buffers.pop(run_id, None)
            self.answers._answer_flush_at.pop(run_id, None)
            self.answers._answer_start_pending.discard(run_id)
            raise

        final_answer = loop_result["answer"]
        result = loop_result["result"]
        status = loop_result["status"]
        if status == "waiting_user":
            self.answers._answer_buffers.pop(run_id, None)
            self.answers._answer_flush_at.pop(run_id, None)
            await self.answers._ensure_answer_stream_started(repo, run_id)
            await repo.add_event(run_id, "answer.paused", {"status": status})
            await repo.session.commit()
        else:
            await self.answers._complete_answer_stream(repo, run_id, final_answer.summary)
        await finalize_trusted_run(repo, run_id, final_answer, result, status)

    async def _profile_for_run(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        snapshot: dict[str, Any],
    ) -> AgentProfile:
        return AgentProfile.from_snapshot(snapshot)


async def execute_run_in_process(run_id: str, settings: AstraRuntimeSettings) -> None:
    try:
        execution = RunExecution(settings)
    except ModelConfigurationError as exc:
        logger.exception("run.engine.configuration_error run_id=%s", run_id)
        async with SessionLocal() as session:
            repo = RunUnitOfWork(session)
            error = run_error_from_exception(exc)
            await repo.add_event(run_id, "run.error", error)
            await repo.update_run_status(run_id, "blocked", summary=error["message"], result=error_result(error))
        return
    try:
        await execution.run(run_id)
    except asyncio.CancelledError:
        async with SessionLocal() as session:
            await RunUnitOfWork(session).cancel_run(run_id)
        raise
    finally:
        await execution.model_client.aclose()


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
