from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import AgentTurnRecord, StepRecord


class RunStepTurnStore:
    async def create_step(
        self,
        run_id: str,
        index: int,
        title: str,
        intent: str,
        *,
        depends_on: list[str] | None = None,
    ) -> StepRecord:
        step = StepRecord(
            run_id=run_id,
            index=index,
            title=title,
            intent=intent,
            status="pending",
            depends_on=depends_on or [],
        )
        self.session.add(step)
        await self.session.flush()
        await self.add_event(
            run_id,
            "step.created",
            {"step_id": step.id, "index": step.index, "title": step.title, "status": step.status},
        )
        await self.session.flush()
        return step

    async def update_step(
        self,
        step_id: str,
        status: str,
        *,
        evidence: dict[str, Any] | None = None,
    ) -> StepRecord:
        step = await self._require_step(step_id)
        step.status = status
        if status == "running" and step.started_at is None:
            step.started_at = utc_now()
        if status in {"completed", "failed", "skipped"}:
            step.completed_at = utc_now()
        if evidence is not None:
            step.evidence = evidence
        run = await self.require_run(step.run_id)
        run.current_step_id = step.id
        run.updated_at = utc_now()
        await self.add_event(
            step.run_id,
            "step.updated",
            {"step_id": step.id, "index": step.index, "status": step.status, "evidence": evidence},
        )
        await self.session.flush()
        return step

    async def create_agent_turn(
        self,
        run_id: str,
        turn_index: int,
        decision_type: str,
        reasoning_summary: str,
        *,
        selected_tool: str | None = None,
        decision: dict[str, Any] | None = None,
        memory_reads: list[dict[str, Any]] | None = None,
        state_version_before: int | None = None,
        plan_version: int = 1,
        phase: str = "created",
        idempotency_key: str | None = None,
        plan_node_id: str | None = None,
        node_execution_id: str | None = None,
        agent_execution_id: str | None = None,
    ) -> AgentTurnRecord:
        now = utc_now()
        turn = AgentTurnRecord(
            run_id=run_id,
            agent_execution_id=agent_execution_id,
            plan_node_id=plan_node_id,
            node_execution_id=node_execution_id,
            turn_index=turn_index,
            decision_type=decision_type,
            reasoning_summary=reasoning_summary,
            selected_tool=selected_tool,
            decision=decision or {},
            memory_reads=memory_reads or [],
            memory_writes=[],
            status="created",
            state_version_before=state_version_before,
            plan_version=plan_version,
            phase=phase,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        self.session.add(turn)
        await self.session.flush()
        await self.add_event(
            run_id,
            "agent_turn.created",
            {
                "turn_id": turn.id,
                "turn_index": turn.turn_index,
                "node_execution_id": node_execution_id,
                "decision_type": decision_type,
                "selected_tool": selected_tool,
                "reasoning_summary": reasoning_summary,
                "agent_execution_id": agent_execution_id,
            },
            agent_execution_id=agent_execution_id,
        )
        await self.session.flush()
        return turn

    async def update_agent_turn(
        self,
        turn_id: str,
        *,
        status: str | None = None,
        observation: dict[str, Any] | None = None,
        reflection: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        artifact_id: str | None = None,
        memory_writes: list[dict[str, Any]] | None = None,
        evaluation: dict[str, Any] | None = None,
        reflection_patch: dict[str, Any] | None = None,
        state_version_after: int | None = None,
        phase: str | None = None,
        paused_node: str | None = None,
    ) -> AgentTurnRecord:
        turn = await self._require_agent_turn(turn_id)
        updates = {
            "status": status,
            "observation": observation,
            "reflection": reflection,
            "tool_call_id": tool_call_id,
            "artifact_id": artifact_id,
            "memory_writes": memory_writes,
            "evaluation": evaluation,
            "reflection_patch": reflection_patch,
            "state_version_after": state_version_after,
            "phase": phase,
            "paused_node": paused_node,
        }
        for field_name, value in updates.items():
            if value is not None:
                setattr(turn, field_name, value)
        turn.updated_at = utc_now()
        await self.add_event(
            turn.run_id,
            "agent_turn.updated",
            {
                "turn_id": turn.id,
                "turn_index": turn.turn_index,
                "status": turn.status,
                "observation": observation,
                "reflection": reflection,
            },
        )
        await self.session.flush()
        return turn

    async def _require_step(self, step_id: str) -> StepRecord:
        result = await self.session.execute(select(StepRecord).where(StepRecord.id == step_id))
        step = result.scalar_one_or_none()
        if step is None:
            raise ValueError(f"Step not found: {step_id}")
        return step

    async def _require_agent_turn(self, turn_id: str) -> AgentTurnRecord:
        result = await self.session.execute(
            select(AgentTurnRecord).where(AgentTurnRecord.id == turn_id)
        )
        turn = result.scalar_one_or_none()
        if turn is None:
            raise ValueError(f"AgentTurn not found: {turn_id}")
        return turn

    async def start_tool_call(
        self,
        run_id: str,
        step_id: str | None,
        tool_name: str,
        tool_version: str,
        tool_input: dict[str, Any],
        permission: str,
        side_effect_level: str,
        *,
        plan_node_id: str | None = None,
        node_execution_id: str | None = None,
        status: str = "running",
        agent_execution_id: str | None = None,
    ) -> ToolCallRecord:
        call = ToolCallRecord(
            run_id=run_id,
            agent_execution_id=agent_execution_id,
            step_id=step_id,
            plan_node_id=plan_node_id,
            node_execution_id=node_execution_id,
            tool_name=tool_name,
            tool_version=tool_version,
            input=tool_input,
            status=status,
            permission=permission,
            side_effect_level=side_effect_level,
            started_at=utc_now(),
        )
        self.session.add(call)
        await self.session.flush()
        await self.add_event(
            run_id,
            "tool_call.proposed" if status == "awaiting_approval" else "tool_call.started",
            {
                "tool_call_id": call.id,
                "step_id": step_id,
                "plan_node_id": plan_node_id,
                "node_execution_id": node_execution_id,
                "tool_name": tool_name,
                "status": status,
                "agent_execution_id": agent_execution_id,
            },
            agent_execution_id=agent_execution_id,
        )
        await self.session.flush()
        return call

    async def transition_tool_call(self, tool_call_id: str, status: str) -> ToolCallRecord:
        call = await self._require_tool_call(tool_call_id)
        call.status = status
        if status in {"rejected", "failed", "cancelled"}:
            call.completed_at = utc_now()
        await self.add_event(
            call.run_id,
            "tool_call.started" if status == "running" else "tool_call.status_changed",
            {"tool_call_id": call.id, "tool_name": call.tool_name, "status": status},
            agent_execution_id=call.agent_execution_id,
        )
        await self.session.flush()
        return call

    async def get_approved_tool_call(self, run_id: str) -> ToolCallRecord | None:
        result = await self.session.execute(
            select(ToolCallRecord)
            .where(ToolCallRecord.run_id == run_id, ToolCallRecord.status == "approved")
            .options(selectinload(ToolCallRecord.approval_request))
            .order_by(ToolCallRecord.started_at)
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def finish_tool_call(
        self,
        tool_call_id: str,
        *,
        output: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
    ) -> ToolCallRecord:
        call = await self._require_tool_call(tool_call_id)
        call.output = output
        call.error = error
        call.status = "failed" if error else "succeeded"
        call.completed_at = utc_now()
        await self.add_event(
            call.run_id,
            "tool_call.completed",
            {
                "tool_call_id": call.id,
                "step_id": call.step_id,
                "tool_name": call.tool_name,
                "status": call.status,
                "error": error,
                "agent_execution_id": call.agent_execution_id,
            },
            agent_execution_id=call.agent_execution_id,
        )
        await self.session.flush()
        return call

    async def _require_tool_call(self, tool_call_id: str) -> ToolCallRecord:
        result = await self.session.execute(
            select(ToolCallRecord).where(ToolCallRecord.id == tool_call_id)
        )
        call = result.scalar_one_or_none()
        if call is None:
            raise ValueError(f"ToolCall not found: {tool_call_id}")
        return call
