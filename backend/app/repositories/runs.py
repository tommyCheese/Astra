import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentTurnRecord,
    ApprovalGrantRecord,
    ApprovalRequestRecord,
    ArtifactRecord,
    BudgetReservationRecord,
    MemoryRecord,
    ModelInvocationRecord,
    NodeExecutionRecord,
    PlanNodeRecord,
    PlanRecord,
    ResourceLeaseRecord,
    RunEventRecord,
    RunRecord,
    SandboxJobRecord,
    StepRecord,
    TaskRecord,
    ToolCallRecord,
    utc_now,
)
from app.schemas.agent import (
    AgentState,
    AnswerMode,
    AssuranceLevel,
    ContractMode,
    PlanExecution,
    PlanGraphSnapshotEvent,
    PlanRevisionEvent,
    ReasoningPolicySnapshot,
    RunExecutionProfile,
    RunResult,
)


def run_detail_options():
    return (
        selectinload(RunRecord.steps),
        selectinload(RunRecord.task),
        selectinload(RunRecord.tool_calls),
        selectinload(RunRecord.artifacts),
        selectinload(RunRecord.events),
        selectinload(RunRecord.turns),
        selectinload(RunRecord.memories),
        selectinload(RunRecord.sandbox_jobs),
        selectinload(RunRecord.approval_requests),
        selectinload(RunRecord.approval_grants),
        selectinload(RunRecord.node_executions).selectinload(
            NodeExecutionRecord.resource_leases
        ),
        selectinload(RunRecord.node_executions).selectinload(
            NodeExecutionRecord.budget_reservations
        ),
        selectinload(RunRecord.plans).selectinload(PlanRecord.nodes),
        selectinload(RunRecord.plans).selectinload(PlanRecord.edges),
    )


class RunRepository:
    TERMINAL_STATUSES = frozenset(
        {
            "completed",
            "completed_with_warnings",
            "failed",
            "blocked",
            "waiting_user",
            "cancelled",
        }
    )

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_task_run(
        self,
        goal: str,
        model_policy: dict[str, Any],
        task_id: str | None = None,
        *,
        reasoning_policy: dict[str, Any] | None = None,
        answer_mode: str = "standard",
        execution_profile: dict[str, Any] | None = None,
        agent_profile_snapshot: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> RunRecord:
        now = utc_now()
        if execution_profile is None and reasoning_policy:
            snapshot = ReasoningPolicySnapshot.model_validate(reasoning_policy)
            generated_profile = RunExecutionProfile(
                answer_mode=AnswerMode.trusted,
                contract_mode=ContractMode.model,
                assurance_level=AssuranceLevel.full,
                reasoning_policy=snapshot,
                plan_execution=PlanExecution.auto,
                validators=["task_adapter", "artifact_reference"],
            )
            answer_mode = AnswerMode.trusted.value
            execution_profile = generated_profile.model_dump(mode="json")
        task = await self.session.get(TaskRecord, task_id) if task_id else None
        if task_id and task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task is None:
            task = TaskRecord(
                title=goal[:240],
                description=goal,
                status="created",
                risk_level="low",
                preferred_answer_mode=answer_mode,
                created_at=now,
                updated_at=now,
            )
        else:
            task.preferred_answer_mode = answer_mode
            task.updated_at = now
        run_policy = {**model_policy, "conversation_goal": goal}
        run = RunRecord(
            task=task,
            status="created",
            mode="web_agent",
            answer_mode=answer_mode,
            execution_profile=deepcopy(execution_profile or {}),
            model_policy=run_policy,
            agent_profile_snapshot=deepcopy(agent_profile_snapshot or {}),
            reasoning_policy=reasoning_policy or {},
            task_adapter="web",
            created_at=now,
            updated_at=now,
        )
        self.session.add(task)
        self.session.add(run)
        await self.session.flush()
        await self.add_event(run.id, "run.created", {"goal": goal, "status": run.status})
        if agent_profile_snapshot:
            await self.add_event(
                run.id,
                "agent_profile.frozen",
                {"profile": safe_agent_profile_manifest(agent_profile_snapshot)},
            )
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()
        return run

    async def freeze_agent_profile_snapshot(
        self,
        run_id: str,
        snapshot: dict[str, Any],
    ) -> RunRecord:
        run = await self.require_run(run_id)
        current = run.agent_profile_snapshot or {}
        if current:
            if current != snapshot:
                raise ValueError("Agent Profile snapshot is immutable")
            return run
        run.agent_profile_snapshot = deepcopy(snapshot)
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "agent_profile.frozen",
            {"profile": safe_agent_profile_manifest(snapshot)},
        )
        await self.session.commit()
        return run

    async def initialize_reasoning_state(
        self,
        run_id: str,
        *,
        task_contract: dict[str, Any],
        plan_graph: dict[str, Any],
        agent_state: dict[str, Any],
    ) -> RunRecord:
        run = await self.require_run(run_id)
        if run.state_version:
            raise ValueError("Reasoning state is already initialized")
        run.task_contract = task_contract
        run.plan_graph = plan_graph
        run.agent_state = agent_state
        run.state_version = int(agent_state.get("version", 1))
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "reasoning.state_initialized",
            {"state_version": run.state_version, "plan_version": plan_graph.get("version", 1)},
        )
        await self.session.commit()
        return run

    async def update_reasoning_state(
        self,
        run_id: str,
        *,
        expected_version: int,
        agent_state: dict[str, Any],
        plan_graph: dict[str, Any] | None = None,
        terminal_reason: dict[str, Any] | None = None,
        waiting_state: dict[str, Any] | None = None,
    ) -> RunRecord:
        run = await self.require_run(run_id)
        if run.state_version != expected_version:
            raise ValueError(
                f"State version conflict: expected {expected_version}, got {run.state_version}"
            )
        next_version = int(agent_state.get("version", expected_version + 1))
        if next_version <= expected_version:
            raise ValueError("State version must increase")
        run.agent_state = agent_state
        run.state_version = next_version
        if plan_graph is not None:
            run.plan_graph = plan_graph
        if terminal_reason is not None:
            run.terminal_reason = terminal_reason
        run.waiting_state = waiting_state
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "reasoning.state_updated",
            {"previous_version": expected_version, "state_version": next_version},
        )
        await self.session.commit()
        return run

    async def set_waiting_state(self, run_id: str, waiting_state: dict[str, Any]) -> RunRecord:
        run = await self.require_run(run_id)
        waiting_state = {
            **waiting_state,
            "continuation_token": waiting_state.get("continuation_token") or str(uuid.uuid4()),
        }
        run.waiting_state = waiting_state
        run.status = "waiting_user"
        run.updated_at = utc_now()
        await self.add_event(run_id, "run.waiting_user", waiting_state)
        await self.session.commit()
        return run

    async def resume_waiting_run(
        self, run_id: str, observation: dict[str, Any], *, continuation_token: str | None = None
    ) -> RunRecord:
        run = await self.require_run(run_id)
        if run.status != "waiting_user" or not run.waiting_state:
            raise ValueError("Run is not waiting for user input")
        expected_token = run.waiting_state.get("continuation_token")
        if expected_token and continuation_token != expected_token:
            raise ValueError("Invalid continuation token")
        state = dict(run.agent_state or {})
        observations = list(state.get("observations", []))
        observations.append(observation)
        state["observations"] = observations
        state["version"] = int(state.get("version", run.state_version)) + 1
        contract = dict(state.get("task_contract", run.task_contract or {}))
        contract["ambiguity_status"] = "clear"
        contract["clarification_question"] = None
        state["task_contract"] = contract
        run.task_contract = contract
        run.agent_state = state
        run.state_version = state["version"]
        run.waiting_state = None
        run.status = "executing"
        run.completed_at = None
        run.updated_at = utc_now()
        await self.add_event(
            run_id, "run.resumed", {"observation": observation, "state_version": run.state_version}
        )
        await self.session.commit()
        return run

    async def confirm_waiting_plan(
        self,
        run_id: str,
        *,
        continuation_token: str,
        plan_id: str,
        expected_plan_version: int,
        expected_state_version: int,
    ) -> RunRecord:
        from app.repositories.plans import PlanRepository, plan_to_view
        from app.schemas.agent import AgentState

        run = await self.require_run(run_id)
        waiting = run.waiting_state or {}
        if run.status != "waiting_user" or waiting.get("kind") != "plan_confirmation":
            raise ValueError("Run is not waiting for plan confirmation")
        bindings = {
            "continuation_token": continuation_token,
            "plan_id": plan_id,
            "plan_version": expected_plan_version,
            "state_version": expected_state_version,
        }
        if any(waiting.get(key) != value for key, value in bindings.items()):
            raise ValueError("Invalid or stale plan confirmation")
        if run.state_version != expected_state_version:
            raise ValueError("Invalid or stale plan confirmation")
        plan_repository = PlanRepository(self.session)
        plan = await plan_repository.require(plan_id)
        if (
            plan.run_id != run_id
            or plan.version != expected_plan_version
            or plan.status != "planned"
        ):
            raise ValueError("Invalid or stale plan confirmation")
        plan = await plan_repository.activate(
            plan_id, expected_version=expected_plan_version
        )
        state = AgentState.model_validate(run.agent_state)
        state.active_plan_id = plan.id
        state.active_plan_version = plan.version
        state.active_executions = []
        state.version = expected_state_version + 1
        run.agent_state = state.model_dump(mode="json")
        run.state_version = state.version
        run.plan_graph = plan_to_view(plan).model_dump(mode="json")
        run.waiting_state = None
        run.status = "executing"
        run.completed_at = None
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "plan.confirmed",
            {
                "plan_id": plan.id,
                "plan_version": plan.version,
                "state_version": state.version,
            },
        )
        await self.session.commit()
        return run

    async def claim_plan_revision(
        self,
        run_id: str,
        *,
        continuation_token: str,
        plan_id: str,
        expected_plan_version: int,
        expected_state_version: int,
    ) -> tuple[RunRecord, PlanRecord]:
        from app.repositories.plans import PlanRepository

        run = await self.require_run(run_id)
        waiting = run.waiting_state or {}
        bindings = {
            "continuation_token": continuation_token,
            "plan_id": plan_id,
            "plan_version": expected_plan_version,
            "state_version": expected_state_version,
        }
        if (
            run.status != "waiting_user"
            or waiting.get("kind") != "plan_confirmation"
            or any(waiting.get(key) != value for key, value in bindings.items())
            or run.state_version != expected_state_version
        ):
            raise ValueError("Invalid or stale plan revision")
        plan = await PlanRepository(self.session).require(plan_id)
        if (
            plan.run_id != run_id
            or plan.version != expected_plan_version
            or plan.status != "planned"
        ):
            raise ValueError("Invalid or stale plan revision")
        claimed = await self.session.execute(
            update(RunRecord)
            .where(
                RunRecord.id == run_id,
                RunRecord.status == "waiting_user",
                RunRecord.state_version == expected_state_version,
            )
            .values(status="planning", waiting_state=None, updated_at=utc_now())
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise ValueError("Invalid or stale plan revision")
        await self.add_event(
            run_id,
            "plan.revision.started",
            PlanRevisionEvent(
                plan_id=plan.id,
                plan_version=plan.version,
                state_version=run.state_version,
            ).model_dump(mode="json"),
        )
        await self.session.commit()
        return await self.require_run(run_id), plan

    async def reject_plan_revision(
        self,
        run_id: str,
        *,
        plan_id: str,
        plan_version: int,
        state_version: int,
        error_code: str,
    ) -> RunRecord:
        run = await self.require_run(run_id)
        if run.status != "planning" or run.state_version != state_version:
            raise ValueError("Plan revision state changed")
        waiting_state = {
            "kind": "plan_confirmation",
            "plan_id": plan_id,
            "plan_version": plan_version,
            "state_version": state_version,
            "request": "计划调整失败，可修改要求后重试或执行当前版本。",
            "continuation_token": str(uuid.uuid4()),
        }
        run.status = "waiting_user"
        run.waiting_state = waiting_state
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "plan.revision.rejected",
            PlanRevisionEvent(
                plan_id=plan_id,
                plan_version=plan_version,
                state_version=state_version,
                error_code=error_code,
            ).model_dump(mode="json"),
        )
        await self.add_event(run_id, "run.waiting_user", waiting_state)
        await self.session.commit()
        return run

    async def complete_plan_revision(
        self,
        run_id: str,
        *,
        previous_plan: PlanRecord,
        revised_plan: PlanRecord,
    ) -> RunRecord:
        from app.repositories.plans import plan_to_view

        run = await self.require_run(run_id)
        state = AgentState.model_validate(run.agent_state)
        if (
            run.status != "planning"
            or state.active_plan_version != previous_plan.version
            or revised_plan.supersedes_plan_id != previous_plan.id
        ):
            raise ValueError("Plan revision state changed")
        previous_plan.status = "superseded"
        state.active_plan_id = None
        state.active_plan_version = revised_plan.version
        state.active_executions = []
        state.version = run.state_version + 1
        graph = plan_to_view(revised_plan)
        waiting_state = {
            "kind": "plan_confirmation",
            "plan_id": revised_plan.id,
            "plan_version": revised_plan.version,
            "state_version": state.version,
            "request": "调整后的计划已生成，确认后执行。",
            "continuation_token": str(uuid.uuid4()),
        }
        run.agent_state = state.model_dump(mode="json")
        run.state_version = state.version
        run.plan_graph = graph.model_dump(mode="json")
        run.status = "waiting_user"
        run.waiting_state = waiting_state
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "plan.revision.completed",
            PlanRevisionEvent(
                plan_id=previous_plan.id,
                plan_version=previous_plan.version,
                state_version=state.version,
                revised_plan_id=revised_plan.id,
                revised_plan_version=revised_plan.version,
            ).model_dump(mode="json"),
        )
        await self.add_event(
            run_id,
            "plan.graph.snapshot",
            PlanGraphSnapshotEvent(
                plan_id=revised_plan.id,
                plan_version=revised_plan.version,
                graph=graph,
            ).model_dump(mode="json"),
        )
        await self.add_event(run_id, "run.waiting_user", waiting_state)
        await self.session.commit()
        return run

    async def get_run(self, run_id: str) -> RunRecord | None:
        result = await self.session.execute(
            select(RunRecord)
            .where(RunRecord.id == run_id)
            .execution_options(populate_existing=True)
            .options(*run_detail_options())
        )
        return result.scalar_one_or_none()

    async def get_run_status(self, run_id: str) -> str | None:
        result = await self.session.execute(select(RunRecord.status).where(RunRecord.id == run_id))
        return result.scalar_one_or_none()

    async def list_recent_runs(self, limit: int = 100) -> list[RunRecord]:
        result = await self.session.execute(
            select(RunRecord)
            .order_by(RunRecord.created_at.desc())
            .limit(limit)
            .options(*run_detail_options())
        )
        return list(result.scalars().all())

    async def require_run(self, run_id: str) -> RunRecord:
        run = await self.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    async def list_task_runs(self, task_id: str) -> list[RunRecord]:
        result = await self.session.execute(
            select(RunRecord).where(RunRecord.task_id == task_id).order_by(RunRecord.created_at)
        )
        return list(result.scalars().all())

    async def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        summary: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> None:
        run = await self.require_run(run_id)
        if run.status == "cancelled" and status != "cancelled":
            return
        run.status = status
        run.updated_at = utc_now()
        run.task.updated_at = run.updated_at
        if status == "planning" and run.started_at is None:
            run.started_at = utc_now()
        if status in {"completed", "completed_with_warnings", "failed", "blocked", "cancelled"}:
            run.completed_at = utc_now()
        if summary is not None:
            run.summary = summary
        if result is not None:
            run.result = result
        await self.add_event(run_id, "run.status_changed", {"status": status})
        await self.session.commit()

    async def cancel_run(self, run_id: str) -> RunRecord:
        run = await self.require_run(run_id)
        if run.status in self.TERMINAL_STATUSES and run.status != "waiting_user":
            return run

        now = utc_now()
        partial_answer = "".join(
            str(event.payload.get("delta", ""))
            for event in sorted(run.events, key=lambda item: item.id)
            if event.type == "answer.delta" and isinstance(event.payload, dict)
        ).strip()
        summary = partial_answer or "已终止本次运行。"
        terminal_reason = {
            "category": "user_cancelled",
            "reason": "用户主动终止当前运行。",
            "partial_answer": bool(partial_answer),
        }
        cancelled_executions = [
            execution
            for execution in run.node_executions
            if execution.status in {"active", "waiting"}
        ]

        await self.session.execute(
            update(StepRecord)
            .where(StepRecord.run_id == run_id, StepRecord.status.in_(["pending", "running"]))
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
        await self.session.execute(
            update(ToolCallRecord)
            .where(ToolCallRecord.run_id == run_id, ToolCallRecord.status == "running")
            .values(
                status="cancelled",
                completed_at=now,
                error={"category": "user_cancelled", "message": "工具调用已由用户终止。"},
            )
        )
        await self.session.execute(
            update(AgentTurnRecord)
            .where(
                AgentTurnRecord.run_id == run_id,
                AgentTurnRecord.status.in_(["created", "running"]),
            )
            .values(status="cancelled", phase="cancelled", updated_at=now)
        )
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
                error={"category": "user_cancelled", "message": "沙箱任务已由用户终止。"},
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

        run.status = "cancelled"
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
        agent_state = dict(run.agent_state or {})
        agent_state["active_executions"] = []
        agent_state.pop("active_node_id", None)
        agent_state["version"] = int(
            agent_state.get("version", run.state_version or 0)
        ) + 1
        run.agent_state = agent_state
        run.state_version = agent_state["version"]
        run.completed_at = now
        run.updated_at = now
        run.task.updated_at = now
        for execution in cancelled_executions:
            await self.add_event(
                run_id,
                "plan.node.execution_cancelled",
                {
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
                },
            )
        await self.add_event(run_id, "run.cancelled", terminal_reason)
        await self.session.commit()
        return await self.require_run(run_id)

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
        await self.session.commit()
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
        await self.session.commit()
        return step

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
    ) -> ToolCallRecord:
        call = ToolCallRecord(
            run_id=run_id,
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
            },
        )
        await self.session.commit()
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
        )
        await self.session.commit()
        return call

    async def create_approval_request(
        self,
        *,
        run_id: str,
        turn_id: str,
        tool_call_id: str,
        tool_name: str,
        tool_version: str,
        frozen_input: dict[str, Any],
        input_hash: str,
        preview: str,
        permission: str,
        impact: str,
        similar_matcher: dict[str, Any] | None,
        frozen_effect_plan: dict[str, Any] | None = None,
        effect_plan_hash: str | None = None,
        analyzer_version: str | None = None,
        analyzer_digest: str | None = None,
        reviewer_identity: dict[str, Any] | None = None,
        node_execution_id: str | None = None,
        execution_attempt: int | None = None,
        expected_execution_state_version: int | None = None,
    ) -> ApprovalRequestRecord:
        request = ApprovalRequestRecord(
            run_id=run_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            node_execution_id=node_execution_id,
            execution_attempt=execution_attempt,
            expected_execution_state_version=expected_execution_state_version,
            tool_name=tool_name,
            tool_version=tool_version,
            frozen_input=deepcopy(frozen_input),
            input_hash=input_hash,
            frozen_effect_plan=deepcopy(frozen_effect_plan or {}),
            effect_plan_hash=effect_plan_hash,
            analyzer_version=analyzer_version,
            analyzer_digest=analyzer_digest,
            reviewer_identity=deepcopy(reviewer_identity),
            preview=preview,
            permission=permission,
            impact=impact,
            similar_matcher=deepcopy(similar_matcher),
            status="pending",
        )
        self.session.add(request)
        await self.session.flush()
        await self.add_event(
            run_id,
            "approval.requested",
            {
                "approval_id": request.id,
                "tool_call_id": tool_call_id,
                "node_execution_id": node_execution_id,
                "execution_attempt": execution_attempt,
                "expected_execution_state_version": expected_execution_state_version,
                "tool_name": tool_name,
                "preview": preview,
                "permission": permission,
                "impact": impact,
                "allow_similar": similar_matcher is not None,
                "effect_plan_hash": effect_plan_hash,
                "action_summary": (frozen_effect_plan or {}).get("summary"),
                "effect_kinds": [
                    item.get("kind")
                    for item in (frozen_effect_plan or {}).get("effects", [])
                    if isinstance(item, dict)
                ],
                "resources": [
                    item.get("resource")
                    for item in (frozen_effect_plan or {}).get("effects", [])
                    if isinstance(item, dict)
                ],
            },
        )
        await self.session.commit()
        return request

    async def decide_approval(
        self,
        run_id: str,
        approval_id: str,
        decision: str,
        *,
        continuation_token: str,
        reviewer_identity: dict[str, Any] | None = None,
        rejection_guidance: str | None = None,
    ) -> tuple[ApprovalRequestRecord, ToolCallRecord]:
        run = await self.require_run(run_id)
        if run.status != "waiting_user" or not run.waiting_state:
            raise ValueError("Run is not waiting for approval")
        if run.waiting_state.get("approval_id") != approval_id:
            raise ValueError("Approval is not pending for this run")
        if run.waiting_state.get("continuation_token") != continuation_token:
            raise ValueError("Invalid continuation token")
        request = await self.session.get(ApprovalRequestRecord, approval_id)
        if request is None or request.run_id != run_id or request.status != "pending":
            raise ValueError("Approval has already been decided")
        if request.node_execution_id:
            execution = await self.session.get(
                NodeExecutionRecord,
                request.node_execution_id,
            )
            if (
                execution is None
                or execution.attempt != request.execution_attempt
                or execution.state_version != request.expected_execution_state_version
                or execution.current_slot != "current"
            ):
                raise ValueError("Approval is bound to a stale NodeExecution attempt")
        if decision in {"allow_similar", "allow_task"} and request.similar_matcher is None:
            raise ValueError("Similar approval is not available")
        if reviewer_identity and reviewer_identity.get("identity_type") in {
            "main_agent", "subagent", "tool_runtime", "external_provider"
        }:
            raise ValueError("Agent identities cannot approve their own actions")
        decided_at = utc_now()
        claimed = await self.session.execute(
            update(ApprovalRequestRecord)
            .where(
                ApprovalRequestRecord.id == approval_id,
                ApprovalRequestRecord.run_id == run_id,
                ApprovalRequestRecord.status == "pending",
            )
            .values(
                status="approved" if decision != "reject" else "rejected",
                decision=decision,
                decided_at=decided_at,
                reviewer_identity=deepcopy(reviewer_identity),
            )
        )
        if claimed.rowcount != 1:
            await self.session.rollback()
            raise ValueError("Approval has already been decided")
        await self.session.refresh(request)
        call = await self._require_tool_call(request.tool_call_id)
        call.status = "approved" if decision != "reject" else "rejected"
        if decision == "reject":
            call.completed_at = utc_now()
            turn = await self._require_agent_turn(request.turn_id)
            observation = {
                "kind": "approval_result",
                "status": "rejected",
                "summary": f"User rejected {request.tool_name}",
                "data": {"approved": False, "tool_call_id": call.id},
            }
            if rejection_guidance:
                observation["data"]["guidance"] = rejection_guidance
            turn.status = "completed"
            turn.phase = "committed"
            turn.observation = observation
            state = dict(run.agent_state or {})
            observations = list(state.get("observations", []))
            observations.append(observation)
            state["observations"] = observations
            state["version"] = int(state.get("version", run.state_version)) + 1
            run.agent_state = state
            run.state_version = state["version"]
        if decision in {"allow_similar", "allow_task"}:
            effect_kinds = [
                effect["kind"]
                for effect in request.frozen_effect_plan.get("effects", [])
                if isinstance(effect, dict) and isinstance(effect.get("kind"), str)
            ]
            proposal = request.similar_matcher or {}
            invocation_constraints = (
                proposal.get("invocation_constraints", {})
                if "effect_kinds" in proposal
                else proposal
            )
            self.session.add(
                ApprovalGrantRecord(
                    run_id=run_id,
                    task_id=run.task_id,
                    scope="task" if decision == "allow_task" else "run",
                    subject=(
                        {"task_id": run.task_id}
                        if decision == "allow_task"
                        else {"run_id": run_id, "task_id": run.task_id}
                    ),
                    tool_name=request.tool_name,
                    tool_version=request.tool_version,
                    matcher=deepcopy(invocation_constraints),
                    effect_kinds=deepcopy(proposal.get("effect_kinds", effect_kinds)),
                    resource_matcher=deepcopy(proposal.get("resource_matcher", {})),
                    invocation_constraints=deepcopy(invocation_constraints),
                    source_approval_id=request.id,
                )
            )
        run.waiting_state = None
        run.status = "executing"
        run.completed_at = None
        run.updated_at = utc_now()
        await self.add_event(
            run_id,
            "approval.decided",
            {
                "approval_id": request.id,
                "tool_call_id": call.id,
                "tool_name": request.tool_name,
                "decision": decision,
                "guidance": rejection_guidance if decision == "reject" else None,
            },
        )
        await self.session.commit()
        return request, call

    async def list_approval_grants(
        self, run_id: str, tool_name: str, tool_version: str
    ) -> list[ApprovalGrantRecord]:
        run = await self.require_run(run_id)
        now = utc_now()
        result = await self.session.execute(
            select(ApprovalGrantRecord).where(
                ApprovalGrantRecord.tool_name == tool_name,
                ApprovalGrantRecord.tool_version == tool_version,
                ApprovalGrantRecord.status == "active",
                ApprovalGrantRecord.revoked_at.is_(None),
                (ApprovalGrantRecord.expires_at.is_(None) | (ApprovalGrantRecord.expires_at > now)),
                (
                    (ApprovalGrantRecord.scope == "run")
                    & (ApprovalGrantRecord.run_id == run_id)
                    | (ApprovalGrantRecord.scope == "task")
                    & (ApprovalGrantRecord.task_id == run.task_id)
                ),
            )
        )
        return [
            grant
            for grant in result.scalars().all()
            if grant.max_uses is None or grant.use_count < grant.max_uses
        ]

    async def consume_approval_grant(self, grant_id: str) -> ApprovalGrantRecord:
        return (await self.consume_approval_grants([grant_id]))[0]

    async def consume_approval_grants(
        self, grant_ids: list[str] | tuple[str, ...]
    ) -> list[ApprovalGrantRecord]:
        ordered_ids = sorted(set(grant_ids))
        if not ordered_ids:
            return []
        result = await self.session.execute(
            select(ApprovalGrantRecord).where(ApprovalGrantRecord.id.in_(ordered_ids))
        )
        by_id = {grant.id: grant for grant in result.scalars().all()}
        missing = [grant_id for grant_id in ordered_ids if grant_id not in by_id]
        if missing:
            raise ValueError(f"Approval Grant not found: {missing[0]}")
        now = utc_now()
        grants = [by_id[grant_id] for grant_id in ordered_ids]
        for grant in grants:
            if grant.status != "active" or grant.revoked_at is not None:
                raise ValueError("Approval Grant is not active")
            if grant.expires_at is not None:
                expires_at = grant.expires_at
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=now.tzinfo)
                if expires_at <= now:
                    raise ValueError("Approval Grant has expired")
            if grant.max_uses is not None and grant.use_count >= grant.max_uses:
                raise ValueError("Approval Grant usage limit is exhausted")
        for grant in grants:
            consumed = await self.session.execute(
                update(ApprovalGrantRecord)
                .where(
                    ApprovalGrantRecord.id == grant.id,
                    ApprovalGrantRecord.status == "active",
                    ApprovalGrantRecord.revoked_at.is_(None),
                    ApprovalGrantRecord.use_count == grant.use_count,
                )
                .values(use_count=grant.use_count + 1, last_used_at=now)
            )
            if consumed.rowcount != 1:
                await self.session.rollback()
                raise ValueError("Approval Grant changed while being consumed")
        await self.session.commit()
        for grant in grants:
            await self.session.refresh(grant)
        return grants

    async def revoke_approval_grant(self, grant_id: str) -> ApprovalGrantRecord:
        grant = await self.session.get(ApprovalGrantRecord, grant_id)
        if grant is None:
            raise ValueError(f"Approval Grant not found: {grant_id}")
        if grant.revoked_at is None:
            grant.status = "revoked"
            grant.revoked_at = utc_now()
            await self.session.commit()
        return grant

    async def invalidate_approval_grants_for_tool_identity(
        self,
        run_id: str,
        *,
        tool_name: str,
        tool_version: str,
        schema_digest: str | None = None,
        analyzer_digest: str | None = None,
    ) -> list[ApprovalGrantRecord]:
        run = await self.require_run(run_id)
        result = await self.session.execute(
            select(ApprovalGrantRecord).where(
                ApprovalGrantRecord.tool_name == tool_name,
                ApprovalGrantRecord.status == "active",
                (
                    (ApprovalGrantRecord.scope == "run")
                    & (ApprovalGrantRecord.run_id == run_id)
                    | (ApprovalGrantRecord.scope == "task")
                    & (ApprovalGrantRecord.task_id == run.task_id)
                ),
            )
        )
        invalidated: list[ApprovalGrantRecord] = []
        expected = {
            "tool_version": tool_version,
            "schema_digest": schema_digest,
            "analyzer_digest": analyzer_digest,
        }
        for grant in result.scalars().all():
            constraints = grant.invocation_constraints or {}
            if any(
                constraints.get(key) is not None and constraints[key] != value
                for key, value in expected.items()
            ):
                grant.status = "invalidated"
                grant.revoked_at = utc_now()
                invalidated.append(grant)
        if invalidated:
            await self.session.commit()
        return invalidated

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
            },
        )
        await self.session.commit()
        return call

    async def create_artifact(
        self,
        run_id: str,
        artifact_type: str,
        *,
        content_ref: str | None = None,
        path: str | None = None,
        metadata: dict[str, Any] | None = None,
        tool_call_id: str | None = None,
        sandbox_job_id: str | None = None,
        mime_type: str | None = None,
        size_bytes: int = 0,
        checksum: str | None = None,
        storage_key: str | None = None,
        security_status: str = "pending",
        provenance: dict[str, Any] | None = None,
        plan_node_id: str | None = None,
    ) -> ArtifactRecord:
        artifact = ArtifactRecord(
            run_id=run_id,
            type=artifact_type,
            path=path,
            content_ref=content_ref,
            metadata_=metadata or {},
            tool_call_id=tool_call_id,
            plan_node_id=plan_node_id,
            sandbox_job_id=sandbox_job_id,
            mime_type=mime_type,
            size_bytes=size_bytes,
            checksum=checksum,
            storage_key=storage_key,
            security_status=security_status,
            provenance=provenance or {},
        )
        self.session.add(artifact)
        await self.session.flush()
        await self.add_event(
            run_id,
            "artifact.created",
            {"artifact_id": artifact.id, "type": artifact.type, "path": artifact.path},
        )
        await self.session.commit()
        return artifact

    async def get_artifact_with_workspace(self, artifact_id: str):
        result = await self.session.execute(
            select(ArtifactRecord, TaskRecord.workspace_id)
            .join(RunRecord, ArtifactRecord.run_id == RunRecord.id)
            .join(TaskRecord, RunRecord.task_id == TaskRecord.id)
            .where(ArtifactRecord.id == artifact_id)
        )
        return result.one_or_none()

    async def list_artifacts(self, run_id: str | None = None) -> list[ArtifactRecord]:
        query = select(ArtifactRecord)
        if run_id is not None:
            query = query.where(ArtifactRecord.run_id == run_id)
        result = await self.session.execute(
            query.order_by(ArtifactRecord.created_at, ArtifactRecord.id)
        )
        return list(result.scalars().all())

    async def create_sandbox_job(
        self,
        run_id: str,
        *,
        tool_call_id: str | None,
        executor: str,
        runtime_profile: dict[str, Any],
        resource_limits: dict[str, Any],
        input_artifact_ids: list[str] | None = None,
    ) -> SandboxJobRecord:
        job = SandboxJobRecord(
            run_id=run_id,
            tool_call_id=tool_call_id,
            executor=executor,
            runtime_profile=runtime_profile,
            resource_limits=resource_limits,
            input_artifact_ids=input_artifact_ids or [],
            output_artifact_ids=[],
        )
        self.session.add(job)
        await self.session.flush()
        await self.add_event(
            run_id,
            "sandbox_job.created",
            {"sandbox_job_id": job.id, "tool_call_id": tool_call_id, "status": job.status},
        )
        await self.session.commit()
        return job

    async def transition_sandbox_job(self, job_id: str, status: str, **updates) -> SandboxJobRecord:
        from app.sandbox.runtime import transition

        job = await self.session.get(SandboxJobRecord, job_id)
        if job is None:
            raise ValueError(f"SandboxJob not found: {job_id}")
        transition(job.status, status)
        job.status = status
        if status == "running":
            job.started_at = utc_now()
        if status in {"succeeded", "failed", "timed_out", "cancelled"}:
            job.completed_at = utc_now()
        for key, value in updates.items():
            setattr(job, key, value)
        await self.add_event(
            job.run_id,
            "sandbox_job.status_changed",
            {"sandbox_job_id": job.id, "status": status, "exit_reason": job.exit_reason},
        )
        await self.session.commit()
        return job

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
    ) -> AgentTurnRecord:
        now = utc_now()
        turn = AgentTurnRecord(
            run_id=run_id,
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
            },
        )
        await self.session.commit()
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
        if status is not None:
            turn.status = status
        if observation is not None:
            turn.observation = observation
        if reflection is not None:
            turn.reflection = reflection
        if tool_call_id is not None:
            turn.tool_call_id = tool_call_id
        if artifact_id is not None:
            turn.artifact_id = artifact_id
        if memory_writes is not None:
            turn.memory_writes = memory_writes
        if evaluation is not None:
            turn.evaluation = evaluation
        if reflection_patch is not None:
            turn.reflection_patch = reflection_patch
        if state_version_after is not None:
            turn.state_version_after = state_version_after
        if phase is not None:
            turn.phase = phase
        if paused_node is not None:
            turn.paused_node = paused_node
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
        await self.session.commit()
        return turn

    async def create_memory(
        self,
        *,
        scope: str,
        kind: str,
        content: str,
        provenance: dict[str, Any],
        confidence: float,
        run_id: str | None = None,
        workspace_id: str | None = None,
        created_by: str | None = None,
        structured_data: dict[str, Any] | None = None,
        expires_at=None,
    ) -> MemoryRecord:
        if scope in {"workspace", "user"} and (not provenance or confidence is None):
            if run_id:
                await self.add_event(
                    run_id,
                    "memory.write_rejected",
                    {"scope": scope, "kind": kind, "reason": "missing_provenance_or_confidence"},
                )
                await self.session.commit()
            raise ValueError("Persistent memory requires provenance and confidence")
        now = utc_now()
        memory = MemoryRecord(
            run_id=run_id,
            workspace_id=workspace_id,
            created_by=created_by,
            scope=scope,
            kind=kind,
            content=content,
            structured_data=structured_data or {},
            provenance=provenance,
            confidence=confidence,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        self.session.add(memory)
        await self.session.flush()
        if run_id:
            await self.add_event(
                run_id,
                "memory.write",
                {
                    "memory_id": memory.id,
                    "scope": memory.scope,
                    "kind": memory.kind,
                    "confidence": memory.confidence,
                    "provenance": memory.provenance,
                },
            )
        await self.session.commit()
        return memory

    async def list_memories(
        self,
        *,
        scope: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[MemoryRecord]:
        query = select(MemoryRecord).where(MemoryRecord.confidence >= min_confidence)
        if scope:
            query = query.where(MemoryRecord.scope == scope)
        if kind:
            query = query.where(MemoryRecord.kind == kind)
        if run_id:
            query = query.where(MemoryRecord.run_id == run_id)
        query = query.order_by(MemoryRecord.updated_at.desc()).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def add_event(
        self, run_id: str, event_type: str, payload: dict[str, Any]
    ) -> RunEventRecord:
        event = RunEventRecord(run_id=run_id, type=event_type, payload=payload)
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_events(self, run_id: str, after_id: int = 0) -> list[RunEventRecord]:
        result = await self.session.execute(
            select(RunEventRecord)
            .where(RunEventRecord.run_id == run_id, RunEventRecord.id > after_id)
            .order_by(RunEventRecord.id)
        )
        return list(result.scalars().all())

    async def _require_step(self, step_id: str) -> StepRecord:
        result = await self.session.execute(select(StepRecord).where(StepRecord.id == step_id))
        step = result.scalar_one_or_none()
        if step is None:
            raise ValueError(f"Step not found: {step_id}")
        return step

    async def _require_tool_call(self, tool_call_id: str) -> ToolCallRecord:
        result = await self.session.execute(
            select(ToolCallRecord).where(ToolCallRecord.id == tool_call_id)
        )
        call = result.scalar_one_or_none()
        if call is None:
            raise ValueError(f"ToolCall not found: {tool_call_id}")
        return call

    async def _require_agent_turn(self, turn_id: str) -> AgentTurnRecord:
        result = await self.session.execute(
            select(AgentTurnRecord).where(AgentTurnRecord.id == turn_id)
        )
        turn = result.scalar_one_or_none()
        if turn is None:
            raise ValueError(f"AgentTurn not found: {turn_id}")
        return turn


def run_to_view(run: RunRecord) -> dict[str, Any]:
    from app.repositories.plans import plan_to_summary, plan_to_view

    trusted = (run.answer_mode or "trusted") == "trusted"
    result_payload = None
    if run.result is not None:
        raw_result = dict(run.result) if isinstance(run.result, dict) else {}
        raw_result.setdefault("summary", run.summary or "")
        result_payload = RunResult.model_validate(raw_result).model_dump(mode="json")
    active_plan = next(
        (plan for plan in getattr(run, "plans", []) if plan.id == run.active_plan_id), None
    ) if trusted else None
    plan_view = plan_to_view(active_plan) if active_plan is not None else None
    canonical_steps = (
        [
            {
                "id": node.id,
                "plan_id": node.plan_id,
                "plan_version": node.plan_version,
                "node_key": node.node_key,
                "index": node.index,
                "title": node.title,
                "intent": node.intent,
                "status": node.status.value,
                "depends_on": node.depends_on,
                "required_capabilities": node.required_capabilities,
                "required_skill_ids": node.required_skill_ids,
                "success_criteria_refs": node.success_criteria_refs,
                "expected_outcome": node.expected_outcome.model_dump(mode="json")
                if node.expected_outcome
                else None,
                "risk_level": node.risk_level,
                "optional": node.optional,
                "evidence_refs": node.evidence_refs,
                "evidence": {"refs": node.evidence_refs} if node.evidence_refs else None,
                "failure": node.failure,
                "started_at": next(
                    item.started_at for item in active_plan.nodes if item.id == node.id
                ),
                "completed_at": next(
                    item.completed_at for item in active_plan.nodes if item.id == node.id
                ),
            }
            for node in plan_view.nodes
        ]
        if plan_view
        else None
    )
    pending = next(
        (item for item in reversed(run.approval_requests) if item.status == "pending"), None
    )
    execution_payloads = [
        _node_execution_payload(execution) for execution in run.node_executions
    ]
    parallelism = _parallelism_summary(run)
    plan_payload = plan_view.model_dump(mode="json") if plan_view else run.plan_graph or {}
    if trusted and plan_view:
        plan_payload = {
            **plan_payload,
            "active_executions": [
                item
                for item in execution_payloads
                if item["status"] in {"active", "waiting"}
            ],
            "parallelism": parallelism,
        }
    return {
        "id": run.id,
        "task_id": run.task_id,
        "status": run.status,
        "mode": run.mode,
        "answer_mode": run.answer_mode or "trusted",
        "execution_profile": run.execution_profile or {},
        "summary": run.summary,
        "result": result_payload,
        "steps": canonical_steps
        or [
            {
                "id": step.id,
                "index": step.index,
                "title": step.title,
                "intent": step.intent,
                "status": step.status,
                "depends_on": step.depends_on or [],
                "evidence": step.evidence,
                "started_at": step.started_at,
                "completed_at": step.completed_at,
            }
            for step in sorted(run.steps, key=lambda item: item.index)
        ],
        "tool_calls": [
            {
                "id": call.id,
                "step_id": call.step_id,
                "plan_node_id": call.plan_node_id,
                "node_execution_id": call.node_execution_id,
                "tool_name": call.tool_name,
                "tool_version": call.tool_version,
                "input": call.input,
                "output": call.output,
                "status": call.status,
                "permission": call.permission,
                "side_effect_level": call.side_effect_level,
                "started_at": call.started_at,
                "completed_at": call.completed_at,
                "error": call.error,
            }
            for call in run.tool_calls
        ],
        "artifacts": [
            {
                "id": artifact.id,
                "type": artifact.type,
                "path": artifact.path,
                "content_ref": artifact.content_ref,
                "metadata": artifact.metadata_,
                "mime_type": artifact.mime_type,
                "size_bytes": artifact.size_bytes,
                "checksum": artifact.checksum,
                "security_status": artifact.security_status,
                "tool_call_id": artifact.tool_call_id,
                "plan_node_id": artifact.plan_node_id,
                "sandbox_job_id": artifact.sandbox_job_id,
                "provenance": artifact.provenance,
                "content_url": f"/api/artifacts/{artifact.id}/content"
                if artifact.storage_key and artifact.security_status == "verified"
                else None,
                "created_at": artifact.created_at,
            }
            for artifact in run.artifacts
        ],
        "sandbox_jobs": [
            {
                "id": job.id,
                "tool_call_id": job.tool_call_id,
                "status": job.status,
                "executor": job.executor,
                "runtime_profile": job.runtime_profile,
                "resource_limits": job.resource_limits,
                "runtime_name": job.runtime_name,
                "image_digest": job.image_digest,
                "exit_reason": job.exit_reason,
                "error": job.error,
                "stdout_summary": job.stdout_summary,
                "stderr_summary": job.stderr_summary,
                "input_artifact_ids": job.input_artifact_ids,
                "output_artifact_ids": job.output_artifact_ids,
                "created_at": job.created_at,
                "started_at": job.started_at,
                "completed_at": job.completed_at,
            }
            for job in run.sandbox_jobs
        ],
        "events": [
            {
                "id": event.id,
                "type": event.type,
                "payload": event.payload,
                "created_at": event.created_at,
            }
            for event in run.events
        ],
        "turns": [
            {
                "id": turn.id,
                "run_id": turn.run_id,
                "plan_node_id": turn.plan_node_id,
                "node_execution_id": turn.node_execution_id,
                "turn_index": turn.turn_index,
                "decision_type": turn.decision_type,
                "reasoning_summary": turn.reasoning_summary,
                "selected_tool": turn.selected_tool,
                "decision": turn.decision,
                "observation": turn.observation,
                "reflection": turn.reflection,
                "tool_call_id": turn.tool_call_id,
                "artifact_id": turn.artifact_id,
                "memory_reads": turn.memory_reads,
                "memory_writes": turn.memory_writes,
                "status": turn.status,
                "evaluation": turn.evaluation,
                "reflection_patch": turn.reflection_patch,
                "state_version_before": turn.state_version_before,
                "state_version_after": turn.state_version_after,
                "plan_version": turn.plan_version,
                "phase": turn.phase,
                "idempotency_key": turn.idempotency_key,
                "paused_node": turn.paused_node,
                "created_at": turn.created_at,
                "updated_at": turn.updated_at,
            }
            for turn in sorted(run.turns, key=lambda item: item.turn_index)
        ],
        "memories": [
            {
                "id": memory.id,
                "run_id": memory.run_id,
                "scope": memory.scope,
                "kind": memory.kind,
                "content": memory.content,
                "structured_data": memory.structured_data,
                "provenance": memory.provenance,
                "confidence": memory.confidence,
                "created_at": memory.created_at,
                "updated_at": memory.updated_at,
                "expires_at": memory.expires_at,
            }
            for memory in run.memories
        ],
        "chat_messages": build_chat_messages(run),
        "reasoning_policy": run.reasoning_policy or {},
        "task_contract": run.task_contract or {},
        "plan_graph": plan_payload if trusted else {},
        "plan_versions": [
            plan_to_summary(plan).model_dump(mode="json")
            for plan in sorted(getattr(run, "plans", []), key=lambda item: item.version)
        ]
        if trusted
        else [],
        "agent_state": run.agent_state or {},
        "state_version": run.state_version or 0,
        "terminal_reason": run.terminal_reason,
        "waiting_state": run.waiting_state,
        "pending_approval": {
            "id": pending.id,
            "tool_call_id": pending.tool_call_id,
            "node_execution_id": pending.node_execution_id,
            "execution_attempt": pending.execution_attempt,
            "expected_execution_state_version": pending.expected_execution_state_version,
            "tool_name": pending.tool_name,
            "preview": pending.preview,
            "permission": pending.permission,
            "impact": pending.impact,
            "action_summary": pending.frozen_effect_plan.get("summary"),
            "affected_resources": [
                effect.get("resource")
                for effect in pending.frozen_effect_plan.get("effects", [])
                if isinstance(effect, dict) and effect.get("resource")
            ],
            "risk_reason": _approval_risk_reason(pending.frozen_effect_plan),
            "working_directory": pending.frozen_effect_plan.get("cwd"),
            "network_scope": pending.frozen_effect_plan.get("network_scope", {}),
            "effect_kinds": [
                effect.get("kind")
                for effect in pending.frozen_effect_plan.get("effects", [])
                if isinstance(effect, dict) and effect.get("kind")
            ],
            "grant_proposals": (
                [
                    {**pending.similar_matcher, "scope": "run"},
                    {**pending.similar_matcher, "scope": "task"},
                ]
                if pending.similar_matcher is not None
                else []
            ),
            "reviewer_identity": pending.reviewer_identity,
            "decisions": ["approve_once"]
            + (
                ["allow_similar", "allow_task"]
                if pending.similar_matcher is not None
                else []
            )
            + ["reject"],
            "created_at": pending.created_at,
        }
        if pending
        else None,
        "node_executions": execution_payloads,
        "parallelism": parallelism,
        "task_adapter": run.task_adapter or "web",
        "agent_profile": safe_agent_profile_manifest(run.agent_profile_snapshot or {}),
    }


def _approval_risk_reason(effect_plan: dict[str, Any]) -> str | None:
    risky = [
        effect
        for effect in effect_plan.get("effects", [])
        if isinstance(effect, dict)
        and (
            effect.get("persistent")
            or effect.get("reversible") is False
            or effect.get("risk") in {"moderate", "high", "critical"}
        )
    ]
    if not risky:
        return None
    labels = ", ".join(str(effect.get("kind", "unknown")) for effect in risky)
    return f"该操作包含持久化或不可逆影响：{labels}"


def _node_execution_payload(execution: NodeExecutionRecord) -> dict[str, Any]:
    return {
        "execution_id": execution.id,
        "run_id": execution.run_id,
        "plan_id": execution.plan_id,
        "plan_node_id": execution.plan_node_id,
        "plan_version": execution.plan_version,
        "attempt": execution.attempt,
        "dispatch_batch_id": execution.dispatch_batch_id,
        "slot_index": execution.slot_index,
        "worker_id": execution.worker_id,
        "phase": execution.phase,
        "status": execution.status,
        "state_version": execution.state_version,
        "checkpoint": execution.checkpoint,
        "wait_reason": execution.wait_reason,
        "started_at": execution.started_at,
        "heartbeat_at": execution.heartbeat_at,
        "finished_at": execution.finished_at,
        "resource_leases": [
            {
                "id": lease.id,
                "node_execution_id": lease.node_execution_id,
                "resource_summary": lease.resource_summary,
                "mode": lease.mode,
                "fencing_token": lease.fencing_token,
                "acquired_at": lease.acquired_at,
                "expires_at": lease.expires_at,
                "released_at": lease.released_at,
                "release_reason": lease.release_reason,
            }
            for lease in execution.resource_leases
        ],
        "budget_reservations": [
            {
                "id": reservation.id,
                "node_execution_id": reservation.node_execution_id,
                "budget_kind": reservation.budget_kind,
                "reserved": reservation.reserved,
                "consumed": reservation.consumed,
                "status": reservation.status,
                "created_at": reservation.created_at,
                "settled_at": reservation.settled_at,
            }
            for reservation in execution.budget_reservations
        ],
    }


def _parallelism_summary(run: RunRecord) -> dict[str, int]:
    executions = list(getattr(run, "node_executions", []))
    active = [item for item in executions if item.status == "active"]
    waiting = [item for item in executions if item.status == "waiting"]
    budgets = ((run.reasoning_policy or {}).get("effective") or {}).get("budgets") or {}
    total = max(1, int(budgets.get("max_parallel_nodes", 3)))
    used = sum(item.slot_index is not None for item in [*active, *waiting])
    return {
        "requested_slots": total,
        "total_slots": total,
        "used_slots": min(used, total),
        "active_count": len(active),
        "waiting_count": len(waiting),
    }


def safe_agent_profile_manifest(snapshot: dict[str, Any]) -> dict[str, Any]:
    documents = snapshot.get("documents")
    safe_documents: dict[str, dict[str, Any]] = {}
    if isinstance(documents, dict):
        for name, value in documents.items():
            if not isinstance(value, dict):
                continue
            safe_documents[str(name)] = {
                key: value[key]
                for key in ("filename", "sha256", "size_bytes", "status")
                if key in value
            }
    role_documents = snapshot.get("role_documents")
    return {
        "version": str(snapshot.get("version") or "unfrozen"),
        "composition_schema_version": int(snapshot.get("composition_schema_version") or 0),
        "documents": safe_documents,
        "role_documents": role_documents if isinstance(role_documents, dict) else {},
    }


def build_chat_messages(run: RunRecord) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {
            "id": f"{run.id}-user",
            "role": "user",
            "content": run.model_policy.get("conversation_goal", run.task.description),
            "status": "completed",
            "metadata": {"task_id": run.task_id},
        }
    ]
    for turn in sorted(run.turns, key=lambda item: item.turn_index):
        if turn.decision_type == "call_tool":
            content = turn.reasoning_summary
            role = "tool"
        elif turn.decision_type == "reflect":
            content = (turn.reflection or {}).get("summary", turn.reasoning_summary)
            role = "reflection"
        elif turn.decision_type == "finalize":
            content = (run.result or {}).get("summary") or turn.reasoning_summary
            role = "assistant"
        else:
            content = turn.reasoning_summary
            role = "assistant"
        messages.append(
            {
                "id": turn.id,
                "role": role,
                "content": content,
                "status": turn.status,
                "metadata": {
                    "turn_index": turn.turn_index,
                    "decision_type": turn.decision_type,
                    "selected_tool": turn.selected_tool,
                    "observation": turn.observation,
                    "reflection": turn.reflection,
                    "memory_reads": turn.memory_reads,
                    "memory_writes": turn.memory_writes,
                },
            }
        )
    terminal_statuses = {"completed", "completed_with_warnings", "blocked", "failed", "cancelled"}
    if (
        run.status in terminal_statuses
        and run.result
        and not any(message["role"] == "assistant" for message in messages[1:])
    ):
        messages.append(
            {
                "id": f"{run.id}-terminal"
                if run.status in {"blocked", "failed", "cancelled"}
                else f"{run.id}-answer",
                "role": "assistant",
                "content": run.result.get("summary") or run.summary or "任务已完成。",
                "status": run.status,
                "metadata": {"error": run.result.get("error")},
            }
        )
    if run.status == "waiting_user" and run.waiting_state and run.waiting_state.get("request"):
        messages.append(
            {
                "id": f"{run.id}-waiting",
                "role": "assistant",
                "content": str(run.waiting_state["request"]),
                "status": "waiting_user",
                "metadata": {"waiting_state": run.waiting_state},
            }
        )
    return messages
