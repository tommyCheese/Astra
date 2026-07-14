from __future__ import annotations

import secrets

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentTurnRecord,
    ArtifactRecord,
    ConversationShareRecord,
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
from app.repositories.runs import build_chat_messages, run_to_view

TERMINAL_STATUSES = {"completed", "completed_with_warnings", "blocked", "failed", "cancelled"}


class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def list(self, limit: int = 100) -> list[TaskRecord]:
        result = await self.session.execute(
            select(TaskRecord)
            .order_by(TaskRecord.pinned_at.desc().nullslast(), TaskRecord.updated_at.desc())
            .limit(limit)
            .options(selectinload(TaskRecord.runs), selectinload(TaskRecord.share))
        )
        return list(result.scalars().unique().all())

    async def get(self, conversation_id: str, *, detailed: bool = False) -> TaskRecord | None:
        options = [selectinload(TaskRecord.runs), selectinload(TaskRecord.share)]
        result = await self.session.execute(
            select(TaskRecord).where(TaskRecord.id == conversation_id).options(*options)
        )
        task = result.scalar_one_or_none()
        if task and detailed:
            runs = []
            for run in sorted(task.runs, key=lambda item: item.created_at):
                loaded = await self._load_run(run.id)
                if loaded:
                    runs.append(loaded)
            task.runs = runs
        return task

    async def _load_run(self, run_id: str) -> RunRecord | None:
        result = await self.session.execute(
            select(RunRecord).where(RunRecord.id == run_id).options(
                selectinload(RunRecord.task), selectinload(RunRecord.steps),
                selectinload(RunRecord.tool_calls), selectinload(RunRecord.artifacts),
                selectinload(RunRecord.events), selectinload(RunRecord.turns),
                selectinload(RunRecord.memories), selectinload(RunRecord.sandbox_jobs),
            )
        )
        return result.scalar_one_or_none()

    async def update(self, task: TaskRecord, *, title: str | None = None, pinned: bool | None = None) -> TaskRecord:
        if title is not None:
            task.title = title
            task.title_source = "user"
        if pinned is not None:
            task.pinned_at = utc_now() if pinned else None
        task.updated_at = utc_now()
        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def create_or_get_share(self, task: TaskRecord, *, refresh: bool = False) -> ConversationShareRecord:
        share = task.share
        snapshot = self.build_snapshot(task)
        now = utc_now()
        if share and share.active:
            if refresh:
                share.snapshot = snapshot
                share.updated_at = now
            await self.session.commit()
            return share
        if share:
            share.token = secrets.token_urlsafe(32)
            share.snapshot = snapshot
            share.active = True
            share.revoked_at = None
            share.created_at = now
            share.updated_at = now
        else:
            share = ConversationShareRecord(
                conversation=task, token=secrets.token_urlsafe(32), snapshot=snapshot,
                active=True, created_at=now, updated_at=now,
            )
            self.session.add(share)
        await self.session.commit()
        return share

    async def revoke_share(self, task: TaskRecord) -> None:
        if task.share and task.share.active:
            task.share.active = False
            task.share.revoked_at = utc_now()
            task.share.updated_at = utc_now()
            await self.session.commit()

    async def get_public_share(self, token: str) -> ConversationShareRecord | None:
        result = await self.session.execute(
            select(ConversationShareRecord).where(
                ConversationShareRecord.token == token, ConversationShareRecord.active.is_(True)
            )
        )
        return result.scalar_one_or_none()

    async def list_active_shares(self) -> list[ConversationShareRecord]:
        result = await self.session.execute(
            select(ConversationShareRecord)
            .where(ConversationShareRecord.active.is_(True))
            .order_by(ConversationShareRecord.updated_at.desc())
            .options(selectinload(ConversationShareRecord.conversation))
        )
        return list(result.scalars().unique().all())

    def build_snapshot(self, task: TaskRecord) -> dict:
        messages = []
        for run in sorted(task.runs, key=lambda item: item.created_at):
            for message in build_chat_messages(run):
                if message["role"] == "user" or (
                    message["role"] == "assistant" and message["status"] in TERMINAL_STATUSES
                ):
                    messages.append({"role": message["role"], "content": message["content"]})
        return {"title": task.title, "messages": messages}

    async def delete(self, task: TaskRecord) -> list[str]:
        run_ids = [run.id for run in task.runs]
        if any(run.status not in TERMINAL_STATUSES for run in task.runs):
            raise RuntimeError("conversation is active")
        storage_keys: list[str] = []
        if run_ids:
            storage_keys = list((await self.session.scalars(
                select(ArtifactRecord.storage_key).where(
                    ArtifactRecord.run_id.in_(run_ids), ArtifactRecord.storage_key.is_not(None)
                )
            )).all())
            for model in (ArtifactRecord, SandboxJobRecord, ToolCallRecord, StepRecord,
                          RunEventRecord, AgentTurnRecord, MemoryRecord, ModelInvocationRecord):
                await self.session.execute(delete(model).where(model.run_id.in_(run_ids)))
            await self.session.execute(delete(RunRecord).where(RunRecord.id.in_(run_ids)))
        await self.session.execute(delete(ConversationShareRecord).where(ConversationShareRecord.conversation_id == task.id))
        await self.session.execute(delete(TaskRecord).where(TaskRecord.id == task.id))
        await self.session.commit()
        return storage_keys


def conversation_summary(task: TaskRecord) -> dict:
    runs = sorted(task.runs, key=lambda item: item.created_at)
    latest = runs[-1] if runs else None
    return {
        "id": task.id, "title": task.title, "title_source": task.title_source or "auto",
        "pinned_at": task.pinned_at, "created_at": task.created_at, "updated_at": task.updated_at,
        "last_run_status": latest.status if latest else None,
        "last_message_preview": (latest.summary or latest.model_policy.get("conversation_goal", "")) if latest else "",
        "has_active_share": bool(task.share and task.share.active),
    }


def conversation_view(task: TaskRecord) -> dict:
    return {**conversation_summary(task), "runs": [run_to_view(run) for run in sorted(task.runs, key=lambda item: item.created_at)]}
