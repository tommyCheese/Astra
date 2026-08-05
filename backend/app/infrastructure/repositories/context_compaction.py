from __future__ import annotations

from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.schemas.context_compaction import CompactionMetadata, ContextEnvelope
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.executions import (
    AgentExecutionRecord,
    ContextCompactionAttemptRecord,
)


class ContextCompactionAttemptRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def completed(self, metadata: CompactionMetadata) -> ContextCompactionAttemptRecord | None:
        result = await self.session.scalar(
            select(ContextCompactionAttemptRecord).where(
                ContextCompactionAttemptRecord.owner_type == metadata.owner_type.value,
                ContextCompactionAttemptRecord.owner_id == metadata.owner_id,
                ContextCompactionAttemptRecord.window_number == metadata.window_number,
                ContextCompactionAttemptRecord.input_digest == metadata.input_digest,
                ContextCompactionAttemptRecord.policy_version == metadata.policy_version,
                ContextCompactionAttemptRecord.status == "completed",
            )
        )
        return result

    async def start(self, metadata: CompactionMetadata) -> ContextCompactionAttemptRecord:
        record = ContextCompactionAttemptRecord(
            owner_type=metadata.owner_type.value,
            owner_id=metadata.owner_id,
            window_number=metadata.window_number,
            input_digest=metadata.input_digest,
            policy_version=metadata.policy_version,
            checkpoint_schema_version=metadata.checkpoint_schema_version,
            implementation=metadata.implementation.value,
            generation_provider=metadata.generation_provider,
            generation_model=metadata.generation_model,
            status="started",
            state_version=metadata.state_version,
            cancellation_epoch=metadata.cancellation_epoch,
            source_item_ids=list(metadata.source_item_ids),
            retained_tail_ids=list(metadata.retained_tail_ids),
            token_before=metadata.token_before,
        )
        self.session.add(record)
        await self.session.flush()
        return record

    async def install_agent_checkpoint(
        self,
        envelope: ContextEnvelope,
        checkpoint,
        retained_tail_ids: tuple[str, ...],
    ) -> bool:
        current = await self.session.get(AgentExecutionRecord, envelope.owner_id)
        if current is None:
            return False
        continuation = envelope.continuation
        if (
            current.state_version != continuation.state_version
            or current.cancellation_epoch != continuation.cancellation_epoch
        ):
            return False
        payload = {
            **(current.checkpoint or {}),
            "context_checkpoint": checkpoint.model_dump(mode="json"),
            "context_compaction": {
                "schema_version": 2,
                "policy_version": "astra-context-compaction-v2",
                "window_number": continuation.window_number + 1,
                "source_item_ids": list(continuation.source_item_ids),
                "retained_tail_ids": list(retained_tail_ids),
            },
        }
        result = await self.session.execute(
            update(AgentExecutionRecord)
            .where(
                AgentExecutionRecord.id == envelope.owner_id,
                AgentExecutionRecord.state_version == continuation.state_version,
                AgentExecutionRecord.cancellation_epoch == continuation.cancellation_epoch,
            )
            .values(
                checkpoint=payload,
                state_version=AgentExecutionRecord.state_version + 1,
                updated_at=utc_now(),
            )
        )
        return result.rowcount == 1

    async def install_conversation_checkpoint(
        self,
        envelope: ContextEnvelope,
        checkpoint,
        retained_tail_ids: tuple[str, ...],
    ) -> bool:
        task = await self.session.get(TaskRecord, envelope.owner_id)
        if task is None:
            return False
        prior = task.context_state if isinstance(task.context_state, dict) else {}
        if int(prior.get("state_version", 0)) != envelope.continuation.state_version:
            return False
        retained = set(retained_tail_ids)
        folded_source_ids = (
            item for item in envelope.continuation.source_item_ids if item not in retained
        )
        next_state = {
            **prior,
            "version": 2,
            "state_version": envelope.continuation.state_version + 1,
            "window_number": envelope.continuation.window_number + 1,
            "checkpoint": checkpoint.model_dump(mode="json"),
            "retained_tail_ids": list(retained_tail_ids),
            "source_item_ids": list(envelope.continuation.source_item_ids),
            "folded_run_ids": list(
                dict.fromkeys(
                    [
                        *(str(item) for item in prior.get("folded_run_ids", []) if item),
                        *folded_source_ids,
                    ]
                )
            ),
        }
        result = await self.session.execute(
            update(TaskRecord)
            .where(TaskRecord.id == envelope.owner_id, TaskRecord.context_state == prior)
            .values(context_state=next_state, updated_at=utc_now())
        )
        return result.rowcount == 1

    async def finish(
        self,
        record: ContextCompactionAttemptRecord,
        *,
        status: str,
        checkpoint: dict[str, Any] | None = None,
        token_after: int | None = None,
        duration_ms: int | None = None,
        cost_usd: float | None = None,
        usage: dict[str, Any] | None = None,
        failure_stage: str | None = None,
        failure_code: str | None = None,
    ) -> None:
        record.status = status
        record.checkpoint = checkpoint
        record.token_after = token_after
        record.duration_ms = duration_ms
        record.cost_usd = cost_usd
        record.usage = usage or {}
        record.failure_stage = failure_stage
        record.failure_code = failure_code
        record.updated_at = utc_now()
        await self.session.flush()
