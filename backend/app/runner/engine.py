import asyncio
import hashlib
import logging
import time
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent_profile import (
    AgentProfile,
    AgentProfileConfigurationError,
    load_agent_profile,
)
from app.core.config import Settings
from app.core.errors import run_error_from_exception
from app.db.models import RunRecord
from app.db.session import SessionLocal
from app.repositories.plans import PlanRepository, plan_to_view
from app.repositories.runs import RunRepository
from app.runner.agent_loop import AgentLoop
from app.runner.coordinator import RunCoordinator
from app.runner.model_client import ModelConfigurationError, ModelOutputError, build_model_client
from app.runner.node_worker import ReadOnlyAgentNodeExecutor
from app.runner.planning import PlanService, PlanValidationError, canonical_agent_state
from app.runner.reasoning import (
    build_default_contract,
    normalize_contract,
    validate_contract,
)
from app.runner.recovery import ExecutionRecovery
from app.schemas.agent import (
    AnswerMode,
    ExpectedObservation,
    FinalAnswer,
    PlanDraft,
    PlanExecution,
    PlanGraphSnapshotEvent,
    PlanNodeDraft,
    ReasoningPolicySnapshot,
    RunExecutionProfile,
    SuccessCriterion,
    TaskContract,
)
from app.skills.catalog import SkillActivationService
from app.tools.base import ToolRegistry
from app.tools.registry import build_tool_registry
from app.usage_metering import DatabaseUsageRecorder

logger = logging.getLogger("astra.engine")

STREAM_FLUSH_INTERVAL_SECONDS = 0.1
STREAM_FLUSH_MAX_CHARS = 512
_SHARED_MODEL_HTTP_CLIENTS: dict[str, httpx.AsyncClient] = {}


def shared_model_http_client(settings: Settings) -> httpx.AsyncClient | None:
    """Reuse provider connections across Runs in the same server process."""
    if settings.model_provider == "mock":
        return None
    endpoint = settings.model_base_url.rstrip("/")
    client = _SHARED_MODEL_HTTP_CLIENTS.get(endpoint)
    if client is None:
        client = httpx.AsyncClient(
            timeout=60,
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=16),
        )
        _SHARED_MODEL_HTTP_CLIENTS[endpoint] = client
    return client


async def close_shared_model_http_clients() -> None:
    clients = list(_SHARED_MODEL_HTTP_CLIENTS.values())
    _SHARED_MODEL_HTTP_CLIENTS.clear()
    for client in clients:
        await client.aclose()


class RunEngine:
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
        self.tool_registry = tool_registry or build_tool_registry(settings)
        self._answer_buffers: dict[str, str] = {}
        self._answer_flush_at: dict[str, float] = {}

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
            repo = RunRepository(session)
            try:
                await self._run_with_repo(repo, run_id)
            except asyncio.CancelledError:
                await self._flush_cancelled_answer(run_id)
                raise
            except (
                AgentProfileConfigurationError,
                ModelConfigurationError,
                ModelOutputError,
                httpx.RequestError,
            ) as exc:
                logger.exception("run.engine.model_error run_id=%s cause=%s", run_id, str(exc))
                error = run_error_from_exception(exc)
                await repo.add_event(run_id, "run.error", error)
                await repo.update_run_status(
                    run_id, "blocked", summary=error["message"], result=error_result(error)
                )
            except Exception as exc:
                logger.exception("run.engine.failed run_id=%s cause=%s", run_id, type(exc).__name__)
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

    async def _flush_cancelled_answer(self, run_id: str) -> None:
        buffered = self._answer_buffers.pop(run_id, "")
        self._answer_flush_at.pop(run_id, None)
        if not buffered:
            return
        async with SessionLocal() as session:
            repo = RunRepository(session)
            await repo.add_event(run_id, "answer.delta", {"delta": buffered})
            await session.commit()

    async def _run_with_repo(self, repo: RunRepository, run_id: str) -> None:
        run = await repo.require_run_core(run_id)
        profile = await self._profile_for_run(repo, run_id, run.agent_profile_snapshot or {})
        self.model_client.bind_agent_profile(profile)
        self._bind_reasoning_effort(run)
        skill_service = SkillActivationService(
            repo.session,
            max_active=self.settings.skills_max_active,
            max_resource_bytes=self.settings.skills_max_resource_bytes_per_run,
        )
        if self.settings.skills_enabled:
            try:
                skill_blocks = await skill_service.prompt_blocks(run_id)
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
                        "selected": [
                            item["qualified_identity"] for item in skill_blocks
                        ],
                        "phase": "before_task_contract",
                    },
                )
            self._active_skill_blocks = skill_blocks
        goal = await self._conversation_goal(repo, run)
        execution_profile = (
            RunExecutionProfile.model_validate(run.execution_profile)
            if run.execution_profile
            else None
        )
        if execution_profile is None:
            raise ValueError("Run execution profile is required")
        if execution_profile.answer_mode == AnswerMode.standard:
            await self._execute_agent_loop(repo, run_id, goal)
            return

        if run.state_version and run.agent_state:
            await self._execute_trusted_runtime(repo, run_id, goal)
            return

        logger.info("run.phase run_id=%s phase=planning", run_id)
        await repo.add_event(
            run_id,
            "reasoning.phase.started",
            {"phase": "planning", "label": "正在理解任务并制定计划"},
        )
        await repo.update_run_status(run_id, "planning")
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
            len(
                {
                    capability
                    for node in plan.nodes
                    for capability in node.required_capabilities
                }
            ),
        )
        run = await repo.require_run_core(run_id)
        snapshot = ReasoningPolicySnapshot.model_validate(run.reasoning_policy or {})
        contract = contract or build_default_contract(goal)
        capabilities = set(self.tool_registry.specs())
        for spec in self.tool_registry.specs().values():
            capabilities.update(spec.capabilities)
        plan_service = PlanService(PlanRepository(repo.session))
        try:
            canonical_plan = await plan_service.create(
                run_id,
                plan,
                contract=contract,
                capabilities=capabilities,
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
                budgets=snapshot.effective.budgets,
                activate=execution_profile.plan_execution == PlanExecution.auto,
            )
        await repo.session.commit()
        skill_bound_nodes = [
            {
                "plan_node_id": node.id,
                "node_key": node.node_key,
                "required_skill_ids": list(node.required_skill_ids or []),
            }
            for node in canonical_plan.nodes
            if node.required_skill_ids
        ]
        if skill_bound_nodes:
            await repo.add_event(
                run_id,
                "skill.plan_bound",
                {
                    "plan_id": canonical_plan.id,
                    "plan_version": canonical_plan.version,
                    "nodes": skill_bound_nodes,
                },
            )
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
            return
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
            return

        await self._execute_trusted_runtime(repo, run_id, goal)

    async def _execute_trusted_runtime(
        self,
        repo: RunRepository,
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

    async def _prepare_plan(
        self,
        run_id: str,
        goal: str,
        reasoning_policy: dict[str, Any],
        execution_profile: dict[str, Any] | None = None,
    ) -> tuple[TaskContract, PlanDraft]:
        ReasoningPolicySnapshot.model_validate(reasoning_policy)
        RunExecutionProfile.model_validate(execution_profile or {})
        public_goal = self._public_plan_text(goal)
        if self.settings.agent_use_general_runtime:
            try:
                contract_result = await self.model_client.contract(public_goal)
            except ModelOutputError as exc:
                contract_result = exc
        else:
            contract_result = build_default_contract(public_goal)
        contract = self._resolve_contract(run_id, public_goal, contract_result)
        skill_revisions = [
            {
                "qualified_identity": item["qualified_identity"],
                "revision_id": item["revision_id"],
                "digest": item["digest"],
            }
            for item in getattr(self, "_active_skill_blocks", [])
        ]
        if skill_revisions:
            criteria = [
                criterion.model_copy(
                    update={
                        "provenance": {
                            **criterion.provenance,
                            "skill_revisions": skill_revisions,
                        }
                    }
                )
                for criterion in contract.success_criteria
            ]
            known_checks: set[str] = set()
            for block in getattr(self, "_active_skill_blocks", []):
                metadata = block.get("metadata", {})
                checks = (
                    metadata.get("mandatory_checks", [])
                    if isinstance(metadata, dict)
                    else []
                )
                if not isinstance(checks, list):
                    continue
                for raw_check in checks:
                    check = str(raw_check).strip()
                    if not check or check in known_checks:
                        continue
                    known_checks.add(check)
                    identity = block["qualified_identity"]
                    stable_id = hashlib.sha256(
                        f"{identity}\0{check}".encode()
                    ).hexdigest()[:12]
                    criteria.append(
                        SuccessCriterion(
                            id=f"skill-check-{stable_id}",
                            description=check,
                            verification_method="task_adapter",
                            provenance={
                                "kind": "skill_mandatory_check",
                                "qualified_identity": identity,
                                "revision_id": block["revision_id"],
                                "digest": block["digest"],
                            },
                        )
                    )
            contract = contract.model_copy(
                update={
                    "skill_revisions": skill_revisions,
                    "success_criteria": criteria,
                }
            )
        try:
            plan_result = await self.model_client.plan(
                goal,
                contract=contract,
            )
        except ModelOutputError as exc:
            plan_result = exc
        plan = self._resolve_plan(
            run_id,
            plan_result,
            contract=contract,
        )
        active_identities = [item["qualified_identity"] for item in skill_revisions]
        if active_identities:
            plan = plan.model_copy(
                update={
                    "nodes": [
                        node
                        if node.required_skill_ids
                        else node.model_copy(
                            update={"required_skill_ids": active_identities}
                        )
                        for node in plan.nodes
                    ]
                }
            )
        return contract, plan

    def _resolve_contract(
        self, run_id: str, goal: str, result: TaskContract | Exception | None
    ) -> TaskContract:
        contract = result
        if isinstance(result, Exception):
            if not isinstance(result, ModelOutputError):
                raise result
            logger.warning("run.contract.fallback run_id=%s reason=%s", run_id, str(result))
            contract = build_default_contract(goal)
        if contract:
            contract = normalize_contract(contract, goal)
            try:
                validate_contract(contract)
            except ValueError as exc:
                raise ModelOutputError(f"Invalid task contract: {exc}") from exc
        return contract or build_default_contract(goal)

    def _resolve_plan(
        self,
        run_id: str,
        result: PlanDraft | Exception,
        *,
        contract: TaskContract,
    ) -> PlanDraft:
        if not isinstance(result, Exception):
            if result.nodes:
                return result
            logger.warning("run.plan.fallback run_id=%s reason=empty plan nodes", run_id)
            return self._default_plan(
                "生成回复",
                "直接回应用户当前请求",
                contract=contract,
            )
        if not isinstance(result, ModelOutputError):
            raise result
        logger.warning("run.plan.fallback run_id=%s reason=%s", run_id, str(result))
        return self._default_plan(
            "生成回复",
            "直接回应用户当前请求",
            contract=contract,
        )

    @staticmethod
    def _default_plan(
        title: str,
        intent: str,
        *,
        contract: TaskContract,
    ) -> PlanDraft:
        return PlanDraft(
            nodes=[
                PlanNodeDraft(
                    node_key="step-1",
                    title=title,
                    intent=intent,
                    success_criteria_refs=[item.id for item in contract.success_criteria],
                    expected_outcome=ExpectedObservation(
                        kind="step_result",
                        success_condition="step completed with accepted evidence",
                    ),
                    risk_level=contract.risk_level,
                )
            ],
        )

    @staticmethod
    def _public_plan_text(text: str) -> str:
        context_marker = "Conversation context:\n"
        request_marker = "\nCurrent user request: "
        if context_marker not in text or request_marker not in text:
            return text
        prefix, contextual = text.split(context_marker, 1)
        _, current_request = contextual.rsplit(request_marker, 1)
        return prefix + current_request

    async def _conversation_goal(self, repo: RunRepository, run: RunRecord) -> str:
        current_goal = run.model_policy.get("conversation_goal")
        if not current_goal:
            current_goal = (await repo.require_run(run.id)).task.description
        if run.model_policy.get("conversation_context_required") is False:
            return current_goal
        conversation_runs = await repo.list_task_runs(run.task_id)
        previous_runs = [item for item in conversation_runs if item.id != run.id][-6:]
        if not previous_runs:
            return current_goal

        context_lines: list[str] = []
        for item in previous_runs:
            previous_goal = item.model_policy.get("conversation_goal", "")
            context_lines.extend([f"User: {previous_goal}", f"Assistant: {item.summary or ''}"])
        return (
            "Conversation context:\n"
            + "\n".join(context_lines)
            + f"\nCurrent user request: {current_goal}"
        )

    async def _execute_agent_loop(self, repo: RunRepository, run_id: str, goal: str) -> None:
        if not self.settings.agent_use_general_runtime:
            raise RuntimeError(
                "The general Agent runtime is required; the legacy Web workflow has been removed"
            )

        run = await repo.require_run_core(run_id)
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
        await repo.update_run_status(run_id, "executing")
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
                on_answer_delta=lambda delta: self._handle_answer_delta(repo, run_id, delta),
            )
        except Exception:
            self._answer_buffers.pop(run_id, None)
            self._answer_flush_at.pop(run_id, None)
            raise

        final_answer = loop_result["answer"]
        result = loop_result["result"]
        status = loop_result["status"]
        if status == "waiting_user":
            self._answer_buffers.pop(run_id, None)
            self._answer_flush_at.pop(run_id, None)
            await repo.add_event(run_id, "answer.paused", {"status": status})
            await repo.session.commit()
        else:
            await self._complete_answer_stream(repo, run_id, final_answer.summary)
        await self._finalize_agent_loop(repo, run_id, final_answer, result, status)

    async def _finalize_agent_loop(
        self,
        repo: RunRepository,
        run_id: str,
        final_answer: FinalAnswer,
        result: dict[str, Any],
        status: str,
    ) -> None:
        if status == "waiting_user":
            await repo.update_run_status(run_id, status, summary=final_answer.summary)
            logger.info("run.paused run_id=%s status=waiting_user", run_id)
            return
        if result.get("answer_mode") == AnswerMode.standard.value:
            await repo.update_run_status(
                run_id,
                status,
                summary=final_answer.summary,
                result=result,
            )
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
        logger.info(
            "run.complete run_id=%s status=%s findings=%s sources=%s",
            run_id,
            status,
            len(final_answer.findings),
            len(final_answer.sources),
        )

    async def _profile_for_run(
        self,
        repo: RunRepository,
        run_id: str,
        snapshot: dict[str, Any],
    ) -> AgentProfile:
        if snapshot and snapshot.get("version") != "legacy-unversioned":
            return AgentProfile.from_snapshot(snapshot)
        profile = load_agent_profile()
        if not snapshot:
            await repo.freeze_agent_profile_snapshot(run_id, profile.snapshot())
        return profile

    async def _emit_answer_stream(self, repo: RunRepository, run_id: str, content: str) -> None:
        await self._start_answer_stream(repo, run_id)
        await self._answer_delta(repo, run_id, content)
        await self._complete_answer_stream(repo, run_id, content)

    async def _start_answer_stream(self, repo: RunRepository, run_id: str) -> None:
        self._answer_buffers[run_id] = ""
        self._answer_flush_at[run_id] = 0.0
        await repo.add_event(run_id, "answer.started", {"role": "assistant", "mode": "native"})
        await repo.session.commit()

    async def _answer_delta(self, repo: RunRepository, run_id: str, delta: str) -> None:
        if not delta:
            return
        await repo.add_event(run_id, "answer.delta", {"delta": delta})
        await repo.session.commit()

    async def _handle_answer_delta(self, repo: RunRepository, run_id: str, delta: str) -> None:
        if delta == "\0":
            await self._start_answer_stream(repo, run_id)
            return
        if delta == "\1":
            buffered = self._answer_buffers.get(run_id, "")
            self._answer_buffers[run_id] = ""
            if buffered:
                await repo.add_event(run_id, "answer.delta", {"delta": buffered})
            await repo.add_event(
                run_id,
                "answer.settling",
                {"phase": "structuring_and_verifying"},
            )
            await repo.session.commit()
            return
        if not delta:
            return
        buffered = self._answer_buffers.get(run_id, "") + delta
        now = time.monotonic()
        last_flush = self._answer_flush_at.get(run_id, 0.0)
        first_delta = last_flush == 0.0
        should_flush = (
            first_delta
            or now - last_flush >= STREAM_FLUSH_INTERVAL_SECONDS
            or len(buffered) >= STREAM_FLUSH_MAX_CHARS
        )
        if should_flush:
            self._answer_buffers[run_id] = ""
            self._answer_flush_at[run_id] = now
            await self._answer_delta(repo, run_id, buffered)
        else:
            self._answer_buffers[run_id] = buffered

    async def _complete_answer_stream(self, repo: RunRepository, run_id: str, content: str) -> None:
        buffered = self._answer_buffers.pop(run_id, "")
        self._answer_flush_at.pop(run_id, None)
        if buffered:
            await repo.add_event(run_id, "answer.delta", {"delta": buffered})
        await repo.add_event(
            run_id,
            "answer.completed",
            {"content": content, "status": "answer_complete"},
        )
        await repo.session.commit()

    async def _mark_named_step_running(self, repo: RunRepository, run_id: str, name_part: str):
        run = await repo.require_run(run_id)
        for step in sorted(run.steps, key=lambda item: item.index):
            if name_part in step.title or name_part in step.intent:
                await repo.update_step(step.id, "running")
                return step
        return None

    async def _complete_pending_steps(self, repo: RunRepository, run_id: str) -> None:
        run = await repo.require_run(run_id)
        for step in sorted(run.steps, key=lambda item: item.index):
            if step.status in {"pending", "running"}:
                await repo.update_step(
                    step.id,
                    "completed",
                    evidence={"handled_by": "agent_loop"},
                )


async def start_run_in_process(run_id: str, settings: Settings) -> None:
    try:
        engine = RunEngine(settings)
    except ModelConfigurationError as exc:
        logger.exception("run.engine.configuration_error run_id=%s", run_id)
        async with SessionLocal() as session:
            repo = RunRepository(session)
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
            await RunRepository(session).cancel_run(run_id)
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
