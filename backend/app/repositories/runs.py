import uuid
from copy import deepcopy
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentTurnRecord,
    ArtifactRecord,
    MemoryRecord,
    ModelInvocationRecord,
    RunEventRecord,
    RunRecord,
    SandboxJobRecord,
    StepRecord,
    TaskRecord,
    ToolCallRecord,
    utc_now,
)
from app.schemas.agent import RunResult


class RunRepository:
    TERMINAL_STATUSES = {
        "completed",
        "completed_with_warnings",
        "failed",
        "blocked",
        "waiting_user",
        "cancelled",
    }

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_task_run(
        self,
        goal: str,
        model_policy: dict[str, Any],
        task_id: str | None = None,
        *,
        reasoning_policy: dict[str, Any] | None = None,
        agent_profile_snapshot: dict[str, Any] | None = None,
    ) -> RunRecord:
        now = utc_now()
        task = await self.session.get(TaskRecord, task_id) if task_id else None
        if task_id and task is None:
            raise ValueError(f"Task not found: {task_id}")
        if task is None:
            task = TaskRecord(
                title=goal[:240],
                description=goal,
                status="created",
                risk_level="low",
                created_at=now,
                updated_at=now,
            )
        else:
            task.updated_at = now
        run_policy = {**model_policy, "conversation_goal": goal}
        run = RunRecord(
            task=task,
            status="created",
            mode="web_agent",
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
        await self.session.commit()
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

    async def get_run(self, run_id: str) -> RunRecord | None:
        result = await self.session.execute(
            select(RunRecord)
            .where(RunRecord.id == run_id)
            .execution_options(populate_existing=True)
            .options(
                selectinload(RunRecord.steps),
                selectinload(RunRecord.task),
                selectinload(RunRecord.tool_calls),
                selectinload(RunRecord.artifacts),
                selectinload(RunRecord.events),
                selectinload(RunRecord.turns),
                selectinload(RunRecord.memories),
                selectinload(RunRecord.sandbox_jobs),
            )
        )
        return result.scalar_one_or_none()

    async def list_recent_runs(self, limit: int = 100) -> list[RunRecord]:
        result = await self.session.execute(
            select(RunRecord)
            .order_by(RunRecord.created_at.desc())
            .limit(limit)
            .options(
                selectinload(RunRecord.steps),
                selectinload(RunRecord.task),
                selectinload(RunRecord.tool_calls),
                selectinload(RunRecord.artifacts),
                selectinload(RunRecord.events),
                selectinload(RunRecord.turns),
                selectinload(RunRecord.memories),
                selectinload(RunRecord.sandbox_jobs),
            )
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
        if run.status in self.TERMINAL_STATUSES:
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

        await self.session.execute(
            update(StepRecord)
            .where(StepRecord.run_id == run_id, StepRecord.status.in_(["pending", "running"]))
            .values(status="cancelled", completed_at=now)
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
        run.completed_at = now
        run.updated_at = now
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
    ) -> ToolCallRecord:
        call = ToolCallRecord(
            run_id=run_id,
            step_id=step_id,
            tool_name=tool_name,
            tool_version=tool_version,
            input=tool_input,
            status="running",
            permission=permission,
            side_effect_level=side_effect_level,
            started_at=utc_now(),
        )
        self.session.add(call)
        await self.session.flush()
        await self.add_event(
            run_id,
            "tool_call.started",
            {"tool_call_id": call.id, "step_id": step_id, "tool_name": tool_name},
        )
        await self.session.commit()
        return call

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
    ) -> ArtifactRecord:
        artifact = ArtifactRecord(
            run_id=run_id,
            type=artifact_type,
            path=path,
            content_ref=content_ref,
            metadata_=metadata or {},
            tool_call_id=tool_call_id,
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

    async def get_artifact(self, artifact_id: str) -> ArtifactRecord | None:
        return await self.session.get(ArtifactRecord, artifact_id)

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
    ) -> AgentTurnRecord:
        now = utc_now()
        turn = AgentTurnRecord(
            run_id=run_id,
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
    result_payload = None
    if run.result is not None:
        raw_result = dict(run.result) if isinstance(run.result, dict) else {}
        raw_result.setdefault("summary", run.summary or "")
        result_payload = RunResult.model_validate(raw_result).model_dump(mode="json")
    return {
        "id": run.id,
        "task_id": run.task_id,
        "status": run.status,
        "mode": run.mode,
        "summary": run.summary,
        "result": result_payload,
        "steps": [
            {
                "id": step.id,
                "index": step.index,
                "title": step.title,
                "intent": step.intent,
                "status": step.status,
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
        "plan_graph": run.plan_graph or {},
        "agent_state": run.agent_state or {},
        "state_version": run.state_version or 0,
        "terminal_reason": run.terminal_reason,
        "waiting_state": run.waiting_state,
        "task_adapter": run.task_adapter or "web",
        "agent_profile": safe_agent_profile_manifest(run.agent_profile_snapshot or {}),
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
