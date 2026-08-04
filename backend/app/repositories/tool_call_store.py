from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.model_base import utc_now
from app.db.models.permissions import ToolCallRecord


class ToolCallStore:
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
