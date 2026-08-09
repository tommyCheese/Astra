"""Atomic cancellation of a Run and all active descendant work."""

from typing import Any

from sqlalchemy import select, update

from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.executions import (
    AgentExecutionRecord,
    BudgetReservationRecord,
    ModelInvocationRecord,
    NodeExecutionRecord,
    ResourceLeaseRecord,
)
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.plans import PlanNodeRecord, PlanRecord
from app.infrastructure.db.models.runs import AgentTurnRecord, RunRecord, StepRecord
from app.infrastructure.db.models.workspaces import SandboxJobRecord


class RunCancellationStore:
    async def cancel_run(self, run_id: str) -> RunRecord:
        run = await self.require_run(run_id)
        if run.status in self.TERMINAL_STATUSES and run.status != "waiting_user":
            return run
        now = utc_now()
        summary, terminal_reason = self._cancellation_summary(run)
        cancelled_executions = [execution for execution in run.node_executions if execution.status in {"active", "waiting"}]
        await self._cancel_turns_and_tools(run_id, now)
        await self._cancel_execution_resources(run_id, now)
        self._mark_run_cancelled(run, summary, terminal_reason, now)
        await self._record_cancelled_executions(
            run_id,
            cancelled_executions,
            now,
        )
        await self.add_event(run_id, "run.cancelled", terminal_reason)
        await self.session.flush()
        return await self.require_run(run_id)

    @staticmethod
    def _cancellation_summary(run: RunRecord) -> tuple[str, dict[str, Any]]:
        partial_answer = "".join(
            str(event.payload.get("delta", ""))
            for event in sorted(run.events, key=lambda item: item.id)
            if event.type == "answer.delta" and isinstance(event.payload, dict)
        ).strip()
        return partial_answer or "已终止本次运行。", {
            "category": "user_cancelled",
            "reason": "用户主动终止当前运行。",
            "partial_answer": bool(partial_answer),
        }

    async def _cancel_turns_and_tools(self, run_id: str, now) -> None:
        await self.session.execute(
            update(StepRecord)
            .where(
                StepRecord.run_id == run_id,
                StepRecord.status.in_(["pending", "running"]),
            )
            .values(status="cancelled", completed_at=now)
        )
        plan_ids = select(PlanRecord.id).where(PlanRecord.run_id == run_id)
        await self.session.execute(
            update(PlanNodeRecord)
            .where(
                PlanNodeRecord.plan_id.in_(plan_ids),
                PlanNodeRecord.status.in_(["pending", "running"]),
            )
            .values(
                status="blocked",
                completed_at=now,
                failure={"category": "user_cancelled"},
            )
        )
        await self._cancel_tool_calls(run_id, now)
        await self._cancel_agent_turns(run_id, now)
        await self._cancel_sandbox_and_model_calls(run_id, now)

    async def _cancel_tool_calls(self, run_id: str, now) -> None:
        await self.session.execute(
            update(ToolCallRecord)
            .where(ToolCallRecord.run_id == run_id, ToolCallRecord.status == "running")
            .values(
                status="cancelled",
                completed_at=now,
                error={
                    "category": "user_cancelled",
                    "message": "工具调用已由用户终止。",
                },
            )
        )

    async def _cancel_agent_turns(self, run_id: str, now) -> None:
        await self.session.execute(
            update(AgentTurnRecord)
            .where(
                AgentTurnRecord.run_id == run_id,
                AgentTurnRecord.status.in_(["created", "running"]),
            )
            .values(status="cancelled", phase="cancelled", updated_at=now)
        )

    async def _cancel_sandbox_and_model_calls(self, run_id: str, now) -> None:
        await self.session.execute(
            update(SandboxJobRecord)
            .where(
                SandboxJobRecord.run_id == run_id,
                SandboxJobRecord.status.in_(["queued", "preparing", "running", "collecting"]),
            )
            .values(
                status="cancelled",
                completed_at=now,
                exit_reason="user_cancelled",
                error={
                    "category": "user_cancelled",
                    "message": "沙箱任务已由用户终止。",
                },
            )
        )
        await self.session.execute(
            update(ModelInvocationRecord)
            .where(
                ModelInvocationRecord.run_id == run_id,
                ModelInvocationRecord.status == "running",
            )
            .values(status="interrupted", completed_at=now, error_type="CancelledError")
        )

    async def _cancel_execution_resources(self, run_id: str, now) -> None:
        await self.session.execute(
            update(NodeExecutionRecord)
            .where(
                NodeExecutionRecord.run_id == run_id,
                NodeExecutionRecord.status.in_(["active", "waiting"]),
            )
            .values(
                status="cancelled",
                phase="cancelled",
                current_slot=None,
                slot_index=None,
                wait_reason=None,
                failure={"category": "user_cancelled"},
                finished_at=now,
                heartbeat_at=now,
                updated_at=now,
                state_version=NodeExecutionRecord.state_version + 1,
            )
        )
        await self._release_reservations(run_id, now)
        await self._cancel_agent_executions(run_id, now)

    async def _release_reservations(self, run_id: str, now) -> None:
        await self.session.execute(
            update(ResourceLeaseRecord)
            .where(
                ResourceLeaseRecord.run_id == run_id,
                ResourceLeaseRecord.released_at.is_(None),
            )
            .values(released_at=now, release_reason="user_cancelled")
        )
        await self.session.execute(
            update(BudgetReservationRecord)
            .where(
                BudgetReservationRecord.run_id == run_id,
                BudgetReservationRecord.status == "reserved",
            )
            .values(status="cancelled", settled_at=now)
        )

    async def _cancel_agent_executions(self, run_id: str, now) -> None:
        await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.run_id == run_id,
                AgentExecutionRecord.status.not_in(["completed", "completed_with_warnings", "blocked", "failed", "cancelled"]),
            )
            .values(
                status="cancelled",
                phase="terminal",
                wait_reason="user_cancelled",
                error={"category": "user_cancelled", "reason": "用户主动终止当前运行。"},
                worker_id=None,
                fencing_token=AgentExecutionRecord.fencing_token + 1,
                cancellation_epoch=AgentExecutionRecord.cancellation_epoch + 1,
                state_version=AgentExecutionRecord.state_version + 1,
                heartbeat_at=now,
                finished_at=now,
                updated_at=now,
            )
        )

    @staticmethod
    def _mark_run_cancelled(
        run: RunRecord,
        summary: str,
        terminal_reason: dict[str, Any],
        now,
    ) -> None:
        run.status = "cancelled"
        run.cancellation_epoch += 1
        run.summary = summary
        run.result = {
            "summary": summary,
            "findings": [],
            "sources": [],
            "failed_sources": [],
            "source_quality": [],
            "conflicts": [],
            "caveats": ["运行已由用户终止，未继续执行后续步骤。"],
            "verification_notes": ["取消的运行未执行完成验证。"],
        }
        run.terminal_reason = terminal_reason
        run.waiting_state = None
        state = dict(run.agent_state or {})
        state["active_executions"] = []
        state["version"] = int(state.get("version", run.state_version or 0)) + 1
        run.agent_state = state
        run.state_version = state["version"]
        run.completed_at = now
        run.updated_at = now
        run.task.updated_at = now

    async def _record_cancelled_executions(
        self,
        run_id: str,
        executions: list[NodeExecutionRecord],
        now,
    ) -> None:
        for execution in executions:
            await self.add_event(
                run_id,
                "plan.node.execution_cancelled",
                self._cancelled_execution_payload(execution, now),
            )

    @staticmethod
    def _cancelled_execution_payload(execution: NodeExecutionRecord, now) -> dict[str, Any]:
        return {
            "node_execution_id": execution.id,
            "plan_id": execution.plan_id,
            "plan_version": execution.plan_version,
            "plan_node_id": execution.plan_node_id,
            "attempt": execution.attempt,
            "dispatch_batch_id": execution.dispatch_batch_id,
            "slot_index": None,
            "phase": "cancelled",
            "status": "cancelled",
            "state_version": execution.state_version + 1,
            "wait_reason": None,
            "started_at": execution.started_at.isoformat(),
            "heartbeat_at": now.isoformat(),
            "finished_at": now.isoformat(),
        }
