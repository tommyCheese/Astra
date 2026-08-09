"""Load resumable Run state and reconcile interrupted Agent turns."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.application.agent_runtime.services.tooling.approval import input_hash
from app.application.agent_runtime.services.tooling.plugin_runtime import PluginRuntimeState
from app.common.schemas.agent.execution_state import AgentObservation
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.plans import PlanRecord
from app.infrastructure.db.models.runs import AgentTurnRecord, RunRecord
from app.infrastructure.repositories.plans import PlanRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork
from app.infrastructure.tools.base import ToolExecutionError

ToolOutputNormalizer = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class RunRecoveryStage:
    """Own loading and deterministic recovery of persisted execution checkpoints."""

    _repository: RunUnitOfWork
    _plugin_runtime: PluginRuntimeState
    _tool_registry: Any
    _normalize_tool_output: ToolOutputNormalizer

    async def load(
        self,
        run_id: str,
    ) -> tuple[RunRecord, PlanRecord | None]:
        current_task = asyncio.current_task()
        if current_task is not None and current_task.cancelling():
            raise asyncio.CancelledError
        run = await self._repository.require_run_runtime(run_id)
        active_plan = await PlanRepository(self._repository.session).active_for_run(run_id)
        return run, active_plan

    async def recover(
        self,
        run_id: str,
        run: RunRecord,
        observations: list[dict[str, Any]],
    ) -> tuple[
        ToolCallRecord | None,
        AgentTurnRecord | None,
        dict[str, Any] | None,
        tuple[str | None, str | None],
    ]:
        tool_calls = list(run.tool_calls)
        turns = list(run.turns)
        approved_call = await self._approved_call(run_id, tool_calls)
        await self._validate_approved_call(approved_call)
        approved_turn = self._approved_turn(approved_call, turns)
        terminal_status, terminal_summary = await self._recover_latest_turn(
            run_id,
            run,
            observations,
        )
        return (
            approved_call,
            approved_turn,
            self._approval_snapshot(approved_call),
            (terminal_status, terminal_summary),
        )

    async def _approved_call(
        self,
        run_id: str,
        tool_calls: list[ToolCallRecord],
    ) -> ToolCallRecord | None:
        if not tool_calls:
            return None
        return await self._repository.get_approved_tool_call(run_id)

    async def _validate_approved_call(self, tool_call: ToolCallRecord | None) -> None:
        if tool_call is None:
            return
        approval = tool_call.approval_request
        is_valid = (
            approval is not None
            and approval.status == "approved"
            and approval.input_hash == input_hash(tool_call.input)
            and approval.frozen_input == tool_call.input
        )
        if is_valid:
            return
        await self._repository.finish_tool_call(
            tool_call.id,
            error={
                "category": "approval_integrity_error",
                "message": "Approved tool input no longer matches the frozen action",
            },
        )
        raise ToolExecutionError(
            "approval_integrity_error",
            "Approved tool input failed integrity validation",
        )

    @staticmethod
    def _approved_turn(
        tool_call: ToolCallRecord | None,
        turns: list[AgentTurnRecord],
    ) -> AgentTurnRecord | None:
        if tool_call is None:
            return None
        return next((turn for turn in turns if turn.tool_call_id == tool_call.id), None)

    @staticmethod
    def _approval_snapshot(tool_call: ToolCallRecord | None) -> dict[str, Any] | None:
        if tool_call is None or tool_call.approval_request is None:
            return None
        approval = tool_call.approval_request
        return {
            "effect_plan_hash": approval.effect_plan_hash,
            "frozen_effect_plan": dict(approval.frozen_effect_plan or {}),
            "analyzer_version": approval.analyzer_version,
            "analyzer_digest": approval.analyzer_digest,
            "catalog_digest": approval.catalog_digest,
        }

    async def _recover_latest_turn(
        self,
        run_id: str,
        run: RunRecord,
        observations: list[dict[str, Any]],
    ) -> tuple[str | None, str | None]:
        turns = list(run.turns)
        tool_calls = list(run.tool_calls)
        if not turns:
            return None, None
        latest_turn = max(turns, key=lambda turn: turn.turn_index)
        tool_call = next(
            (call for call in tool_calls if call.id == latest_turn.tool_call_id),
            None,
        )
        if latest_turn.phase == "result_recorded" and tool_call and tool_call.output is not None:
            await self._replay_recorded_result(run_id, latest_turn, tool_call, observations)
        elif latest_turn.phase == "executing" and tool_call and tool_call.status == "running":
            return await self._recover_interrupted_call(run_id, run, latest_turn, tool_call)
        return None, None

    async def _replay_recorded_result(
        self,
        run_id: str,
        turn: AgentTurnRecord,
        tool_call: ToolCallRecord,
        observations: list[dict[str, Any]],
    ) -> None:
        tool_output = self._normalize_tool_output(tool_call.tool_name, tool_call.output)
        tool_output.update(
            tool_call_id=tool_call.id,
            plan_node_id=tool_call.plan_node_id,
            node_execution_id=tool_call.node_execution_id,
        )
        try:
            spec = self._tool_registry.get(tool_call.tool_name).spec
            observation = self._plugin_runtime.process(
                spec,
                tool_call.input,
                tool_output,
            ).observation
        except ToolExecutionError:
            observation = AgentObservation(
                kind="tool_result",
                status="succeeded",
                summary=f"{tool_call.tool_name} recovered from checkpoint",
                data=tool_output,
            )
        serialized_observation = observation.model_dump(mode="json")
        observations.append(serialized_observation)
        await self._repository.update_agent_turn(
            turn.id,
            status="completed",
            observation=serialized_observation,
            phase="committed",
        )
        await self._repository.add_event(
            run_id,
            "reasoning.checkpoint_recovered",
            {"turn_id": turn.id, "action": "replay_result"},
        )

    async def _recover_interrupted_call(
        self,
        run_id: str,
        run: RunRecord,
        turn: AgentTurnRecord,
        tool_call: ToolCallRecord,
    ) -> tuple[str | None, str | None]:
        if tool_call.side_effect_level != "read_only":
            summary = "上一次非幂等行动的执行结果未知，需要用户确认后继续。"
            await self._repository.set_waiting_state(
                run_id,
                {
                    "paused_node": "execute",
                    "state_version": run.state_version,
                    "plan_version": (run.agent_state or {}).get("active_plan_version", 1),
                    "request": summary,
                },
            )
            return "waiting_user", summary
        await self._repository.finish_tool_call(
            tool_call.id,
            error={"category": "interrupted", "message": "Recovered after interruption"},
        )
        await self._repository.update_agent_turn(turn.id, status="failed", phase="failed")
        await self._repository.add_event(
            run_id,
            "reasoning.checkpoint_recovered",
            {
                "turn_id": turn.id,
                "action": "retry_same_idempotency_key",
                "idempotency_key": turn.idempotency_key,
            },
        )
        return None, None
