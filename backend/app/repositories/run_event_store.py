from copy import deepcopy
from typing import Any

from sqlalchemy import and_, func, select

from app.db.models.executions import AgentExecutionRecord
from app.db.models.runs import RunEventRecord, RunRecord


class RunEventStore:
    async def add_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        flush: bool = True,
        agent_execution_id: str | None = None,
    ) -> RunEventRecord:
        event_payload = deepcopy(payload)
        if agent_execution_id is not None:
            execution = await self.session.get(AgentExecutionRecord, agent_execution_id)
            if execution is not None and execution.run_id == run_id:
                event_payload = {
                    **event_payload,
                    "agent_execution_id": execution.id,
                    "parent_execution_id": execution.parent_execution_id,
                    "agent_status": execution.status,
                    "agent_phase": execution.phase,
                    "agent_wait_reason": execution.wait_reason,
                    "agent_budget_usage": deepcopy(execution.budget_usage or {}),
                    "causation_id": next(
                        (
                            str(event_payload[key])
                            for key in (
                                "tool_call_id",
                                "node_execution_id",
                                "approval_id",
                                "request_id",
                            )
                            if event_payload.get(key) is not None
                        ),
                        None,
                    ),
                }
        event = RunEventRecord(
            run_id=run_id,
            agent_execution_id=agent_execution_id,
            type=event_type,
            payload=event_payload,
        )
        self.session.add(event)
        if flush:
            await self.session.flush()
        return event

    async def list_events(self, run_id: str, after_id: int = 0) -> list[RunEventRecord]:
        result = await self.session.execute(
            select(RunEventRecord)
            .where(RunEventRecord.run_id == run_id, RunEventRecord.id > after_id)
            .order_by(RunEventRecord.id)
        )
        return list(result.scalars().all())

    async def event_cursor_counts(
        self, run_id: str, through_id: int = 0
    ) -> tuple[int, dict[str, int]]:
        conditions = [RunEventRecord.run_id == run_id]
        if through_id > 0:
            conditions.append(RunEventRecord.id <= through_id)
        run_sequence = int(
            await self.session.scalar(select(func.count(RunEventRecord.id)).where(*conditions)) or 0
        )
        rows = (
            await self.session.execute(
                select(
                    RunEventRecord.agent_execution_id,
                    func.count(RunEventRecord.id),
                )
                .where(*conditions, RunEventRecord.agent_execution_id.is_not(None))
                .group_by(RunEventRecord.agent_execution_id)
            )
        ).all()
        return run_sequence, {str(agent_id): int(count) for agent_id, count in rows}

    async def list_events_with_status(
        self, run_id: str, after_id: int = 0
    ) -> tuple[list[RunEventRecord], str | None]:
        """Load an SSE event batch and terminal status in one database round trip."""
        result = await self.session.execute(
            select(RunRecord.status, RunEventRecord)
            .outerjoin(
                RunEventRecord,
                and_(
                    RunEventRecord.run_id == RunRecord.id,
                    RunEventRecord.id > after_id,
                ),
            )
            .where(RunRecord.id == run_id)
            .order_by(RunEventRecord.id)
        )
        rows = result.all()
        if not rows:
            return [], None
        return [event for _, event in rows if event is not None], rows[0][0]
