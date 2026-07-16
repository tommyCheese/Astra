import asyncio
import logging
import time
from typing import Any

import httpx

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
from app.runner.model_client import ModelConfigurationError, ModelOutputError, build_model_client
from app.runner.planning import PlanService, canonical_agent_state, plan_output_to_draft
from app.runner.reasoning import (
    build_default_contract,
    normalize_contract,
    validate_contract,
)
from app.schemas.agent import (
    AnswerMode,
    ContractMode,
    FinalAnswer,
    PlanOutput,
    PlanStep,
    ReasoningPolicySnapshot,
    RunExecutionProfile,
    TaskContract,
)
from app.tools.base import ToolRegistry
from app.tools.registry import build_tool_registry
from app.usage_metering import DatabaseUsageRecorder

logger = logging.getLogger("astra.engine")


class RunEngine:
    def __init__(
        self,
        settings: Settings,
        *,
        model_client=None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.settings = settings
        self.model_client = model_client or build_model_client(settings)
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
        run = await repo.require_run(run_id)
        profile = await self._profile_for_run(repo, run_id, run.agent_profile_snapshot or {})
        self.model_client.bind_agent_profile(profile)
        self._bind_reasoning_effort(run)
        goal = await self._conversation_goal(repo, run)
        execution_profile = (
            RunExecutionProfile.model_validate(run.execution_profile)
            if run.execution_profile
            else None
        )
        policy_snapshot = ReasoningPolicySnapshot.model_validate(run.reasoning_policy or {})
        if (
            execution_profile is not None
            and execution_profile.answer_mode == AnswerMode.standard
            and policy_snapshot.effective.execution_mode.value != "plan_only"
        ):
            await self._execute_agent_loop(repo, run_id, goal)
            return

        if run.state_version and run.agent_state:
            await self._execute_agent_loop(repo, run_id, goal)
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
            "run.plan.ready run_id=%s steps=%s tools=%s",
            run_id,
            len(plan.steps),
            len(plan.required_tools),
        )
        run = await repo.require_run(run_id)
        snapshot = ReasoningPolicySnapshot.model_validate(run.reasoning_policy or {})
        contract = contract or build_default_contract(goal)
        draft = plan_output_to_draft(
            plan,
            strategy=snapshot.effective.planning_strategy,
            contract=contract,
        )
        capabilities = set(self.tool_registry.specs())
        for spec in self.tool_registry.specs().values():
            capabilities.update(spec.capabilities)
        canonical_plan = await PlanService(PlanRepository(repo.session)).create(
            run_id,
            draft,
            contract=contract,
            capabilities=capabilities,
            budgets=snapshot.effective.budgets,
            activate=snapshot.effective.execution_mode.value != "plan_only",
        )
        await repo.session.commit()
        if snapshot.effective.execution_mode.value == "plan_only":
            planned_state = canonical_agent_state(
                contract, canonical_plan, policy_version=snapshot.version
            ).model_copy(update={"active_plan_id": None, "active_node_id": None})
            if not run.state_version:
                await repo.initialize_reasoning_state(
                    run_id,
                    task_contract=contract.model_dump(mode="json"),
                    plan_graph=plan_to_view(canonical_plan).model_dump(mode="json"),
                    agent_state=planned_state.model_dump(mode="json"),
                )
            await self._complete_plan_only(repo, run_id, plan)
            return

        state = canonical_agent_state(contract, canonical_plan, policy_version=snapshot.version)
        if not run.state_version:
            await repo.initialize_reasoning_state(
                run_id,
                task_contract=contract.model_dump(mode="json"),
                plan_graph=plan_to_view(canonical_plan).model_dump(mode="json"),
                agent_state=state.model_dump(mode="json"),
            )
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
    ) -> tuple[TaskContract | None, PlanOutput]:
        snapshot = ReasoningPolicySnapshot.model_validate(reasoning_policy)
        profile = (
            RunExecutionProfile.model_validate(execution_profile)
            if execution_profile
            else None
        )
        planning_strategy = snapshot.effective.planning_strategy.value
        plan_only = snapshot.effective.execution_mode.value == "plan_only"
        use_model_contract = (
            profile.contract_mode == ContractMode.model
            if profile
            else planning_strategy != "direct" or plan_only
        )
        if planning_strategy == "direct" and not plan_only:
            contract_result = (
                await self.model_client.contract(goal)
                if use_model_contract and self.settings.agent_use_general_runtime
                else build_default_contract(goal)
            )
            plan_result = self._default_plan("处理请求", "根据任务需要直接回答或选择工具")
            logger.info("run.plan.direct_start run_id=%s", run_id)
        elif planning_strategy == "adaptive" and not plan_only:
            if use_model_contract and self.settings.agent_use_general_runtime:
                try:
                    contract_result = await self.model_client.contract(goal)
                except ModelOutputError as exc:
                    contract_result = exc
            else:
                contract_result = build_default_contract(goal)
            plan_result = self._default_plan(
                "自适应处理", "根据观察决定直接回答、调用工具、反思或重新规划"
            )
            logger.info("run.plan.adaptive_start run_id=%s", run_id)
        elif self.settings.agent_use_general_runtime:
            contract_result, plan_result = await asyncio.gather(
                self.model_client.contract(goal),
                self.model_client.plan(goal),
                return_exceptions=True,
            )
        else:
            contract_result, plan_result = None, await self.model_client.plan(goal)
        contract = self._resolve_contract(run_id, goal, contract_result)
        plan = self._resolve_plan(run_id, plan_result)
        return contract, plan

    def _resolve_contract(
        self, run_id: str, goal: str, result: TaskContract | Exception | None
    ) -> TaskContract | None:
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
        return contract

    def _resolve_plan(self, run_id: str, result: PlanOutput | Exception) -> PlanOutput:
        if not isinstance(result, Exception):
            if result.steps:
                return result
            logger.warning("run.plan.fallback run_id=%s reason=empty plan steps", run_id)
            return self._default_plan("生成回复", "直接回应用户当前请求")
        if not isinstance(result, ModelOutputError):
            raise result
        logger.warning("run.plan.fallback run_id=%s reason=%s", run_id, str(result))
        return self._default_plan("生成回复", "直接回应用户当前请求")

    @staticmethod
    def _default_plan(title: str, intent: str) -> PlanOutput:
        return PlanOutput(
            steps=[PlanStep(title=title, intent=intent)],
            success_criteria=["正确回应用户当前请求"],
            risk_level="low",
        )

    async def _complete_plan_only(self, repo: RunRepository, run_id: str, plan: PlanOutput) -> None:
        summary = "规划已生成，未执行工具或外部操作。"
        result = {
            "summary": summary,
            "findings": [
                {
                    "text": self._public_plan_text(f"{step.title}：{step.intent}"),
                    "source_urls": [],
                }
                for step in plan.steps
            ],
            "sources": [],
            "failed_sources": [],
            "source_quality": [],
            "conflicts": [],
            "caveats": ["当前运行使用仅规划模式。"],
            "verification_notes": ["已在执行前停止。"],
        }
        await self._emit_answer_stream(repo, run_id, summary)
        await repo.update_run_status(run_id, "completed", summary=summary, result=result)
        logger.info(
            "run.complete run_id=%s status=completed mode=plan_only steps=%s",
            run_id,
            len(plan.steps),
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
        current_goal = run.model_policy.get("conversation_goal", run.task.description)
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

        run = await repo.require_run(run_id)
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
        current = await repo.require_run(run_id)
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

    async def _persist_plan(self, repo: RunRepository, run_id: str, plan: PlanOutput) -> None:
        if not plan.steps:
            plan = plan.model_copy(
                update={"steps": [PlanStep(title="生成回复", intent="直接回应用户请求")]}
            )
        for index, step in enumerate(plan.steps, start=1):
            await repo.create_step(
                run_id,
                index,
                step.title,
                step.intent,
                depends_on=[],
            )

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
        should_flush = first_delta or now - last_flush >= 0.02 or len(buffered) >= 96
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
