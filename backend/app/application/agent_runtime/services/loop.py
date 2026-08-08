"""Run the root Agent loop assembled from typed execution stages."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, assert_never

from app.application.agent_runtime.policies.completion import AgentCompletionGate
from app.application.agent_runtime.policies.reasoning import (
    AgentObservationEvaluator,
    AgentReflectionGate,
)
from app.application.agent_runtime.services.plugin_runtime import PluginRuntimeState
from app.application.agent_runtime.services.finalization import FinalizationInput
from app.application.agent_runtime.services.runtime_builder import (
    AgentRuntimeBuilder,
    RootRuntimeAssembly,
)
from app.common.core.config import AstraRuntimeSettings
from app.domain.execution.contracts import (
    BlockedOutcome,
    CompletedOutcome,
    ContinueOutcome,
    ExecutionBudget,
    ExecutionContext,
    FailedOutcome,
    StageOutcome,
    WaitingOutcome,
)
from app.infrastructure.db.models.runs import RunRecord
from app.infrastructure.db.models.skills import RunSkillSnapshotRecord
from app.infrastructure.model_clients.contracts import ModelClient
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import AstraToolRegistry
from app.infrastructure.tools.router import ToolRouter

logger = logging.getLogger("astra.agent_loop")


async def execute_turns(stage: Any, context: ExecutionContext) -> StageOutcome:
    """Run the sole iteration stage until it yields a terminal outcome."""
    for turn_index in range(context.turn_index, context.budget.max_turns):
        context.turn_index = turn_index + 1
        outcome = route_outcome(await stage.execute(context))
        if not isinstance(outcome, ContinueOutcome):
            return outcome
    return BlockedOutcome(
        reason="Agent turn budget exhausted before a terminal outcome.",
        error_code="TURN_BUDGET_EXHAUSTED",
    )


def route_outcome(outcome: StageOutcome) -> StageOutcome:
    match outcome:
        case ContinueOutcome() | WaitingOutcome() | CompletedOutcome():
            return outcome
        case BlockedOutcome() | FailedOutcome():
            return outcome
    assert_never(outcome)


class AstraAgentLoop:
    def __init__(
        self,
        settings: AstraRuntimeSettings,
        *,
        model_client: ModelClient,
        tool_registry: AstraToolRegistry,
        sandbox_provider=None,
    ):
        self.settings = settings
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.sandbox_provider = sandbox_provider
        backends = {"in_process"}
        backends.add("astra.runtime")
        if settings.sandbox_enabled:
            backends.add("sandbox.remote")
        self.router = ToolRouter(tool_registry, available_backends=backends)
        self.plugin_runtime = PluginRuntimeState.from_registry(tool_registry)
        self.evaluator = AgentObservationEvaluator()
        self.reflection_gate = AgentReflectionGate()
        self.completion_gate = AgentCompletionGate()
        self._supervisor_close_tasks: set[asyncio.Task[Any]] = set()

    async def run(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        goal: str,
        on_answer_delta: Callable[[str], Awaitable[None]] | None = None,
        *,
        initial_run: RunRecord | None = None,
        fresh_run: bool = False,
        initial_skill_snapshot: RunSkillSnapshotRecord | None = None,
    ) -> dict[str, Any]:
        runtime = await self._runtime_builder().build(
            repository=repo,
            run_id=run_id,
            goal=goal,
            on_answer_delta=on_answer_delta,
            initial_run=initial_run,
            fresh_run=fresh_run,
            initial_skill_snapshot=initial_skill_snapshot,
        )
        await self._record_runtime_limits(repo, run_id, runtime)
        start_turn = (
            runtime.state.approved_turn.turn_index
            if runtime.state.approved_turn is not None
            else runtime.initial_turn_count + 1
        )
        context = ExecutionContext(
            run_id=run_id,
            task_id=runtime.run.task_id,
            goal=goal,
            budget=ExecutionBudget(
                max_turns=runtime.limits.max_turns,
                max_tool_calls=runtime.limits.max_tool_calls,
                max_reflections=runtime.limits.max_reflections,
                max_replans=runtime.limits.max_replans,
                tool_calls_used=runtime.progress.tool_calls_used,
            ),
            turn_index=start_turn - 1,
            observations=runtime.progress.observations,
            tool_outputs=runtime.tool_outputs,
        )
        outcome = await execute_turns(runtime.iteration_stage, context)
        if isinstance(outcome, BlockedOutcome) and runtime.state.terminal_status is None:
            runtime.state.terminal_status = "blocked"
            runtime.state.terminal_summary = outcome.reason
        return await runtime.finalization_stage.execute(
            FinalizationInput(
                run_id=run_id,
                goal=goal,
                profile=runtime.profile,
                progress=runtime.progress,
                tool_outputs=runtime.tool_outputs,
                streamed_final_answer=runtime.state.streamed_final_answer,
                final_turn_id=runtime.state.final_turn_id,
                terminal_status=runtime.state.terminal_status,
                terminal_summary=runtime.state.terminal_summary,
                required_subagent_missing=runtime.state.required_subagent_missing,
                legacy_standard_mode=runtime.state.legacy_standard_mode,
                workspace_changed=runtime.state.workspace_changed,
                workspace_path=runtime.state.workspace_path,
            )
        )

    def _runtime_builder(self) -> AgentRuntimeBuilder:
        return AgentRuntimeBuilder(
            settings=self.settings,
            model_client=self.model_client,
            tool_registry=self.tool_registry,
            tool_router=self.router,
            plugin_runtime=self.plugin_runtime,
            evaluator=self.evaluator,
            reflection_gate=self.reflection_gate,
            completion_gate=self.completion_gate,
            sandbox_provider=self.sandbox_provider,
            supervisor_close_tasks=self._supervisor_close_tasks,
            normalize_tool_output=self._normalize_tool_output,
        )

    async def _record_runtime_limits(
        self,
        repo: RunUnitOfWork,
        run_id: str,
        runtime: RootRuntimeAssembly,
    ) -> None:
        limits = runtime.limits
        logger.info(
            "agent.policy run_id=%s effort=%s reflection=%s/%s limits=turns:%s tools:%s reflections:%s replans:%s",
            run_id,
            runtime.policy.reasoning_effort.value,
            runtime.policy.reflection_enabled,
            runtime.policy.reflection_trigger.value,
            limits.max_turns,
            limits.max_tool_calls,
            limits.max_reflections,
            limits.max_replans,
        )
        if runtime.state.legacy_standard_mode:
            return
        await repo.add_event(
            run_id,
            "reasoning.runtime_limits",
            {
                "reasoning_effort": runtime.policy.reasoning_effort.value,
                "max_turns": limits.max_turns,
                "max_tool_calls": limits.max_tool_calls,
                "max_reflections": limits.max_reflections,
                "max_replans": limits.max_replans,
            },
        )
        await repo.session.commit()

    def _normalize_tool_output(self, tool_name: str, output: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(output)
        normalized["tool_name"] = tool_name
        return normalized
