import asyncio
import logging
import time
from typing import Any

import httpx

from app.core.config import Settings
from app.core.errors import run_error_from_exception
from app.db.session import SessionLocal
from app.repositories.runs import RunRepository
from app.runner.agent_loop import AgentLoop
from app.runner.model_client import ModelConfigurationError, ModelOutputError, build_model_client
from app.runner.reasoning import (
    build_default_contract,
    build_plan_graph,
    normalize_contract,
    validate_contract,
)
from app.schemas.agent import AgentState, PlanOutput, PlanStep, ReasoningPolicySnapshot
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
            except (ModelConfigurationError, ModelOutputError, httpx.RequestError) as exc:
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

    async def _run_with_repo(self, repo: RunRepository, run_id: str) -> None:
        run = await repo.require_run(run_id)
        current_goal = run.model_policy.get("conversation_goal", run.task.description)
        conversation_runs = await repo.list_task_runs(run.task_id)
        previous_runs = [item for item in conversation_runs if item.id != run.id][-6:]
        if previous_runs:
            context_lines = []
            for item in previous_runs:
                previous_goal = item.model_policy.get("conversation_goal", "")
                context_lines.extend([f"User: {previous_goal}", f"Assistant: {item.summary or ''}"])
            goal = (
                "Conversation context:\n"
                + "\n".join(context_lines)
                + f"\nCurrent user request: {current_goal}"
            )
        else:
            goal = current_goal

        if run.state_version and run.agent_state:
            await repo.update_run_status(run_id, "executing")
            if self.settings.agent_use_general_runtime:
                agent_loop = AgentLoop(
                    self.settings, model_client=self.model_client, tool_registry=self.tool_registry
                )
                await self._start_answer_stream(repo, run_id)
                loop_result = await agent_loop.run(
                    repo,
                    run_id,
                    goal,
                    on_answer_delta=lambda delta: self._handle_answer_delta(repo, run_id, delta),
                )
                await self._complete_answer_stream(repo, run_id, loop_result["answer"].summary)
                await repo.update_run_status(
                    run_id,
                    loop_result["status"],
                    summary=loop_result["answer"].summary,
                    result=loop_result["result"],
                )
                return

        logger.info("run.phase run_id=%s phase=planning", run_id)
        await repo.update_run_status(run_id, "planning")
        initial_snapshot = ReasoningPolicySnapshot.model_validate(run.reasoning_policy or {})
        planning_strategy = initial_snapshot.effective.planning_strategy.value
        plan_only = initial_snapshot.effective.execution_mode.value == "plan_only"
        if planning_strategy == "direct" and not plan_only:
            contract_result = build_default_contract(goal)
            plan_result = PlanOutput(
                steps=[PlanStep(title="处理请求", intent="根据任务需要直接回答或选择工具")],
                success_criteria=["正确回应用户当前请求"],
                risk_level="low",
            )
            logger.info("run.plan.direct_start run_id=%s", run_id)
        elif planning_strategy == "adaptive" and not plan_only:
            if self.settings.agent_use_general_runtime:
                try:
                    contract_result = await self.model_client.contract(goal)
                except ModelOutputError as exc:
                    contract_result = exc
            else:
                contract_result = build_default_contract(goal)
            plan_result = PlanOutput(
                steps=[
                    PlanStep(
                        title="自适应处理", intent="根据观察决定直接回答、调用工具、反思或重新规划"
                    )
                ],
                success_criteria=["正确回应用户当前请求"],
                risk_level="low",
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
        if isinstance(contract_result, Exception):
            if not isinstance(contract_result, ModelOutputError):
                raise contract_result
            logger.warning(
                "run.contract.fallback run_id=%s reason=%s", run_id, str(contract_result)
            )
            contract = build_default_contract(goal)
        else:
            contract = contract_result
        if contract:
            contract = normalize_contract(contract, goal)
            try:
                validate_contract(contract)
            except ValueError as exc:
                raise ModelOutputError(f"Invalid task contract: {exc}") from exc
        if isinstance(plan_result, Exception):
            if not isinstance(plan_result, ModelOutputError):
                raise plan_result
            logger.warning("run.plan.fallback run_id=%s reason=%s", run_id, str(plan_result))
            plan = PlanOutput(
                steps=[PlanStep(title="生成回复", intent="直接回应用户当前请求")],
                success_criteria=["正确回应用户当前请求"],
                risk_level="low",
            )
        else:
            plan = plan_result
        logger.info(
            "run.plan.ready run_id=%s steps=%s tools=%s",
            run_id,
            len(plan.steps),
            len(plan.required_tools),
        )
        await self._persist_plan(repo, run_id, plan)
        run = await repo.require_run(run_id)
        snapshot = ReasoningPolicySnapshot.model_validate(run.reasoning_policy or {})
        if snapshot.effective.execution_mode.value == "plan_only":
            summary = "规划已生成，未执行工具或外部操作。"
            result = {
                "summary": summary,
                "findings": [
                    {"text": f"{step.title}：{step.intent}", "source_urls": []}
                    for step in plan.steps
                ],
                "sources": [],
                "failed_sources": [],
                "source_quality": [],
                "conflicts": [],
                "caveats": ["当前运行使用仅规划模式。"],
                "verification_notes": ["已在执行前停止。"],
            }
            await self._complete_pending_steps(repo, run_id)
            await self._emit_answer_stream(repo, run_id, summary)
            await repo.update_run_status(run_id, "completed", summary=summary, result=result)
            logger.info(
                "run.complete run_id=%s status=completed mode=plan_only steps=%s",
                run_id,
                len(plan.steps),
            )
            return
        if contract:
            graph = build_plan_graph(
                contract,
                snapshot.effective.planning_strategy,
                [step.model_dump() for step in plan.steps],
            )
            state = AgentState(task_contract=contract, policy_version=snapshot.version, plan=graph)
        if contract and not run.state_version:
            await repo.initialize_reasoning_state(
                run_id,
                task_contract=contract.model_dump(mode="json"),
                plan_graph=graph.model_dump(mode="json"),
                agent_state=state.model_dump(mode="json"),
            )
        if contract and contract.ambiguity_status != "clear":
            await repo.set_waiting_state(
                run_id,
                {
                    "paused_node": "build_contract",
                    "state_version": state.version,
                    "plan_version": graph.version,
                    "request": contract.clarification_question,
                },
            )
            return

        logger.info("run.phase run_id=%s phase=executing", run_id)
        await repo.update_run_status(run_id, "executing")
        if self.settings.agent_use_general_runtime:
            agent_loop = AgentLoop(
                self.settings,
                model_client=self.model_client,
                tool_registry=self.tool_registry,
            )
            await self._start_answer_stream(repo, run_id)
            loop_result = await agent_loop.run(
                repo,
                run_id,
                goal,
                on_answer_delta=lambda delta: self._handle_answer_delta(repo, run_id, delta),
            )
            await self._complete_answer_stream(repo, run_id, loop_result["answer"].summary)
            final_answer = loop_result["answer"]
            result = loop_result["result"]
            status = loop_result["status"]

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
            return

        raise RuntimeError(
            "The general Agent runtime is required; the legacy Web workflow has been removed"
        )

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
    await engine.run(run_id)


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
