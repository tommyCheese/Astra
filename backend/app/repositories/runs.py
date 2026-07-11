from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentTurnRecord,
    ArtifactRecord,
    MemoryRecord,
    RunEventRecord,
    RunRecord,
    StepRecord,
    TaskRecord,
    ToolCallRecord,
    utc_now,
)


class RunRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_task_run(self, goal: str, model_policy: Dict[str, Any]) -> RunRecord:
        now = utc_now()
        task = TaskRecord(
            title=goal[:240],
            description=goal,
            status="created",
            risk_level="low",
            created_at=now,
            updated_at=now,
        )
        run = RunRecord(
            task=task,
            status="created",
            mode="web_agent",
            model_policy=model_policy,
            created_at=now,
            updated_at=now,
        )
        self.session.add(task)
        self.session.add(run)
        await self.session.flush()
        await self.add_event(run.id, "run.created", {"goal": goal, "status": run.status})
        await self.session.commit()
        return run

    async def get_run(self, run_id: str) -> Optional[RunRecord]:
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
            )
        )
        return result.scalar_one_or_none()

    async def require_run(self, run_id: str) -> RunRecord:
        run = await self.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    async def update_run_status(
        self,
        run_id: str,
        status: str,
        *,
        summary: Optional[str] = None,
        result: Optional[Dict[str, Any]] = None,
    ) -> None:
        run = await self.require_run(run_id)
        run.status = status
        run.updated_at = utc_now()
        if status == "planning" and run.started_at is None:
            run.started_at = utc_now()
        if status in {"completed", "completed_with_warnings", "failed", "blocked"}:
            run.completed_at = utc_now()
        if summary is not None:
            run.summary = summary
        if result is not None:
            run.result = result
        await self.add_event(run_id, "run.status_changed", {"status": status})
        await self.session.commit()

    async def create_step(
        self,
        run_id: str,
        index: int,
        title: str,
        intent: str,
        *,
        depends_on: Optional[List[str]] = None,
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
        evidence: Optional[Dict[str, Any]] = None,
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
        step_id: Optional[str],
        tool_name: str,
        tool_version: str,
        tool_input: Dict[str, Any],
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
        output: Optional[Dict[str, Any]] = None,
        error: Optional[Dict[str, Any]] = None,
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
        content_ref: Optional[str] = None,
        path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ArtifactRecord:
        artifact = ArtifactRecord(
            run_id=run_id,
            type=artifact_type,
            path=path,
            content_ref=content_ref,
            metadata_=metadata or {},
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

    async def create_agent_turn(
        self,
        run_id: str,
        turn_index: int,
        decision_type: str,
        reasoning_summary: str,
        *,
        selected_tool: Optional[str] = None,
        decision: Optional[Dict[str, Any]] = None,
        memory_reads: Optional[List[Dict[str, Any]]] = None,
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
        status: Optional[str] = None,
        observation: Optional[Dict[str, Any]] = None,
        reflection: Optional[Dict[str, Any]] = None,
        tool_call_id: Optional[str] = None,
        artifact_id: Optional[str] = None,
        memory_writes: Optional[List[Dict[str, Any]]] = None,
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
        provenance: Dict[str, Any],
        confidence: float,
        run_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        created_by: Optional[str] = None,
        structured_data: Optional[Dict[str, Any]] = None,
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
        scope: Optional[str] = None,
        kind: Optional[str] = None,
        run_id: Optional[str] = None,
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> List[MemoryRecord]:
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

    async def add_event(self, run_id: str, event_type: str, payload: Dict[str, Any]) -> RunEventRecord:
        event = RunEventRecord(run_id=run_id, type=event_type, payload=payload)
        self.session.add(event)
        await self.session.flush()
        return event

    async def list_events(self, run_id: str, after_id: int = 0) -> List[RunEventRecord]:
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


def run_to_view(run: RunRecord) -> Dict[str, Any]:
    result = run.result or {}
    verification_report = result.get("verification_report")
    return {
        "id": run.id,
        "task_id": run.task_id,
        "status": run.status,
        "mode": run.mode,
        "summary": run.summary,
        "result": run.result,
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
                "created_at": artifact.created_at,
            }
            for artifact in run.artifacts
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
        "verification_report": verification_report,
    }


def build_chat_messages(run: RunRecord) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = [
        {
            "id": f"{run.id}-user",
            "role": "user",
            "content": run.task.description,
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
    return messages
