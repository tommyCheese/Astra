from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.memory import MemoryValidationError
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.memory import MemoryRecallEventRecord
from app.infrastructure.db.models.runs import RunRecord


class MemoryRecallRepository:
    """Persists recall decisions and their later utility feedback."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_event(
        self,
        *,
        run_id: str,
        query_hash: str,
        policy_version: str,
        namespace_manifest: list[dict[str, str]],
        candidates: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        excluded: list[dict[str, Any]],
        turn_id: str | None = None,
        commit: bool = True,
    ) -> MemoryRecallEventRecord:
        await self._require_run(run_id)
        if len(query_hash) != 64:
            raise MemoryValidationError("Memory recall query hash must be a SHA-256 digest")
        policy_version = str(policy_version or "").strip()
        if not policy_version:
            raise MemoryValidationError("Memory recall policy version is required")
        now = utc_now()
        event = MemoryRecallEventRecord(
            run_id=run_id,
            turn_id=turn_id,
            query_hash=query_hash,
            policy_version=policy_version,
            namespace_manifest=namespace_manifest,
            candidates=candidates,
            selected=selected,
            excluded=excluded,
            feedback={},
            created_at=now,
            updated_at=now,
        )
        self.session.add(event)
        await self.session.flush()
        if commit:
            await self.session.commit()
        return event

    async def record_feedback(
        self,
        recall_event_id: str,
        *,
        outcome: str,
        utility_delta: float,
        details: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> MemoryRecallEventRecord:
        if outcome not in {"helpful", "neutral", "harmful"}:
            raise MemoryValidationError("Unsupported Memory recall outcome")
        if not -1.0 <= utility_delta <= 1.0:
            raise MemoryValidationError("Memory recall utility delta must be between -1 and 1")
        event = await self.session.get(MemoryRecallEventRecord, recall_event_id)
        if event is None:
            raise MemoryValidationError(f"Memory recall event not found: {recall_event_id}")
        event.feedback = {
            "outcome": outcome,
            "utility_delta": utility_delta,
            "details": details or {},
        }
        event.updated_at = utc_now()
        await self.session.flush()
        if commit:
            await self.session.commit()
        return event

    async def _require_run(self, run_id: str) -> None:
        exists = await self.session.scalar(
            select(RunRecord.id)
            .join(TaskRecord, TaskRecord.id == RunRecord.task_id)
            .where(RunRecord.id == run_id)
        )
        if exists is None:
            raise MemoryValidationError(f"Run not found: {run_id}")
