from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.run_management.projections.conversation_process import build_public_process
from app.application.run_management.projections.run_view import run_payload
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import ConversationShareRecord, TaskRecord
from app.infrastructure.db.models.evolution import (
    AgentEvolutionAuditRecord,
    AgentEvolutionCandidateRecord,
    AgentEvolutionSourceRecord,
)
from app.infrastructure.db.models.executions import (
    AgentBudgetReservationRecord,
    AgentExecutionRecord,
    AgentJoinRecord,
    BudgetReservationRecord,
    ModelInvocationRecord,
    NodeExecutionRecord,
    ResourceLeaseRecord,
)
from app.infrastructure.db.models.memory import (
    MemoryAuditRecord,
    MemoryLinkRecord,
    MemoryRecallEventRecord,
    MemorySourceRecord,
    PersistedMemoryRecord,
)
from app.infrastructure.db.models.permissions import (
    AgentDelegationRecord,
    AgentIdentityRecord,
    ApprovalGrantRecord,
    ApprovalRequestRecord,
    CredentialGrantRecord,
    DataFlowStateRecord,
    ToolCallRecord,
    ToolCatalogSnapshotRecord,
)
from app.infrastructure.db.models.plans import PlanEdgeRecord, PlanNodeRecord, PlanRecord
from app.infrastructure.db.models.runs import (
    AgentTurnRecord,
    EvidenceRecord,
    RunEventRecord,
    RunRecord,
    StepRecord,
)
from app.infrastructure.db.models.skills import RunSkillSnapshotRecord
from app.infrastructure.db.models.workspaces import (
    ArtifactRecord,
    SandboxJobRecord,
    TaskWorkspaceRecord,
    WorkspaceChangeRecord,
    WorkspaceCheckpointRecord,
    WorkspaceFileRecord,
)
from app.infrastructure.repositories.run_chat_projection import build_chat_messages
from app.infrastructure.repositories.run_query_store import run_detail_options

TERMINAL_STATUSES = {"completed", "completed_with_warnings", "blocked", "failed", "cancelled"}


def _belongs_to_deleted_run(memory, run_ids) -> bool:
    return memory.namespace_type == "run" and memory.namespace_id in run_ids


@dataclass
class ConversationRepository:
    session: AsyncSession

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
        result = await self.session.execute(select(TaskRecord).where(TaskRecord.id == conversation_id).options(*options))
        task = result.scalar_one_or_none()
        if task and detailed:
            runs = []
            for run in sorted(task.runs, key=lambda item: item.created_at):
                loaded = await self._load_run(run.id)
                trigger = (loaded.execution_profile or {}).get("trigger") if loaded else None
                if loaded and not (isinstance(trigger, dict) and trigger.get("delivery") == "silent"):
                    runs.append(loaded)
            task.runs = runs
        return task

    async def create(
        self,
        *,
        title: str,
        preferred_answer_mode: str = "standard",
    ) -> TaskRecord:
        now = utc_now()
        task = TaskRecord(
            title=title,
            description=title,
            status="created",
            risk_level="low",
            preferred_answer_mode=preferred_answer_mode,
            title_source="user",
            created_at=now,
            updated_at=now,
        )
        self.session.add(task)
        await self.session.commit()
        created = await self.get(task.id)
        assert created is not None
        return created

    async def _load_run(self, run_id: str) -> RunRecord | None:
        result = await self.session.execute(select(RunRecord).where(RunRecord.id == run_id).options(*run_detail_options()))
        return result.scalar_one_or_none()

    @staticmethod
    def _retention_predicates(cutoff: datetime):
        has_runs = select(RunRecord.id).where(RunRecord.task_id == TaskRecord.id).exists()
        has_non_terminal_runs = (
            select(RunRecord.id)
            .where(
                RunRecord.task_id == TaskRecord.id,
                RunRecord.status.not_in(TERMINAL_STATUSES),
            )
            .exists()
        )
        has_active_share = (
            select(ConversationShareRecord.id)
            .where(
                ConversationShareRecord.conversation_id == TaskRecord.id,
                ConversationShareRecord.active.is_(True),
            )
            .exists()
        )
        return (
            TaskRecord.updated_at <= cutoff,
            TaskRecord.pinned_at.is_(None),
            has_runs,
            ~has_non_terminal_runs,
            ~has_active_share,
        )

    async def retention_candidate_ids(self, *, cutoff: datetime, limit: int) -> list[str]:
        result = await self.session.scalars(
            select(TaskRecord.id)
            .where(*self._retention_predicates(cutoff))
            .order_by(TaskRecord.updated_at.asc(), TaskRecord.id.asc())
            .limit(limit)
        )
        return list(result.all())

    async def is_retention_eligible(self, conversation_id: str, *, cutoff: datetime) -> bool:
        result = await self.session.scalar(
            select(TaskRecord.id).where(
                TaskRecord.id == conversation_id,
                *self._retention_predicates(cutoff),
            )
        )
        return result is not None

    async def update(
        self,
        task: TaskRecord,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        preferred_answer_mode: str | None = None,
    ) -> TaskRecord:
        if title is not None:
            task.title = title
            task.title_source = "user"
        if pinned is not None:
            task.pinned_at = utc_now() if pinned else None
        if preferred_answer_mode is not None:
            task.preferred_answer_mode = preferred_answer_mode
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
                conversation=task,
                token=secrets.token_urlsafe(32),
                snapshot=snapshot,
                active=True,
                created_at=now,
                updated_at=now,
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
            chat_messages = build_chat_messages(run)
            messages.extend(
                {"role": message["role"], "content": message["content"]}
                for message in chat_messages
                if message["role"] == "user"
            )
            process_items = build_public_process(run)
            if process_items:
                messages.append({"role": "process", "items": process_items})
            messages.extend(
                {"role": message["role"], "content": message["content"]}
                for message in chat_messages
                if message["role"] == "assistant" and message["status"] in TERMINAL_STATUSES
            )
        return {"title": task.title, "messages": messages}

    async def delete(self, task: TaskRecord) -> list[str]:
        run_ids = [run.id for run in task.runs]
        if any(run.status not in TERMINAL_STATUSES for run in task.runs):
            raise RuntimeError("conversation is active")
        storage_keys: list[str] = []
        if run_ids:
            await self._propagate_derived_source_deletion(run_ids)
            storage_keys = list(
                (
                    await self.session.scalars(
                        select(ArtifactRecord.storage_key).where(
                            ArtifactRecord.run_id.in_(run_ids),
                            ArtifactRecord.storage_key.is_not(None),
                        )
                    )
                ).all()
            )
            workspace = await self.session.scalar(select(TaskWorkspaceRecord).where(TaskWorkspaceRecord.task_id == task.id))
            if workspace is not None:
                for model in (
                    WorkspaceChangeRecord,
                    WorkspaceCheckpointRecord,
                    WorkspaceFileRecord,
                ):
                    await self.session.execute(delete(model).where(model.workspace_id == workspace.id))
                await self.session.delete(workspace)
            identity_ids = await self._identity_ids(run_ids)
            for model in (
                CredentialGrantRecord,
                DataFlowStateRecord,
                ToolCatalogSnapshotRecord,
                RunSkillSnapshotRecord,
            ):
                await self.session.execute(delete(model).where(model.run_id.in_(run_ids)))
            await self.session.execute(delete(MemoryRecallEventRecord).where(MemoryRecallEventRecord.run_id.in_(run_ids)))
            for model in (
                ApprovalGrantRecord,
                ApprovalRequestRecord,
                EvidenceRecord,
                ArtifactRecord,
                SandboxJobRecord,
                ToolCallRecord,
                StepRecord,
                RunEventRecord,
                AgentTurnRecord,
                ModelInvocationRecord,
            ):
                await self.session.execute(delete(model).where(model.run_id.in_(run_ids)))
            await self.session.execute(delete(ResourceLeaseRecord).where(ResourceLeaseRecord.run_id.in_(run_ids)))
            await self.session.execute(delete(BudgetReservationRecord).where(BudgetReservationRecord.run_id.in_(run_ids)))
            await self._delete_execution_graph(run_ids, identity_ids)
            await self.session.execute(delete(RunRecord).where(RunRecord.id.in_(run_ids)))
        await self.session.execute(delete(ConversationShareRecord).where(ConversationShareRecord.conversation_id == task.id))
        await self.session.execute(delete(TaskRecord).where(TaskRecord.id == task.id))
        await self.session.commit()
        return storage_keys

    async def _identity_ids(self, run_ids):
        return list(
            (await self.session.scalars(select(AgentIdentityRecord.id).where(AgentIdentityRecord.run_id.in_(run_ids)))).all()
        )

    async def _delete_execution_graph(self, run_ids, identity_ids) -> None:
        await self.session.execute(
            update(NodeExecutionRecord).where(NodeExecutionRecord.run_id.in_(run_ids)).values(agent_execution_id=None)
        )
        await self.session.execute(
            update(AgentExecutionRecord).where(AgentExecutionRecord.run_id.in_(run_ids)).values(parent_node_execution_id=None)
        )
        for model in (
            AgentJoinRecord,
            AgentBudgetReservationRecord,
            AgentExecutionRecord,
            NodeExecutionRecord,
        ):
            await self.session.execute(delete(model).where(model.run_id.in_(run_ids)))
        plan_ids = select(PlanRecord.id).where(PlanRecord.run_id.in_(run_ids))
        for model in (PlanEdgeRecord, PlanNodeRecord):
            await self.session.execute(delete(model).where(model.plan_id.in_(plan_ids)))
        await self.session.execute(delete(PlanRecord).where(PlanRecord.run_id.in_(run_ids)))
        if identity_ids:
            await self.session.execute(
                delete(AgentDelegationRecord).where(
                    AgentDelegationRecord.parent_identity_id.in_(identity_ids)
                    | AgentDelegationRecord.child_identity_id.in_(identity_ids)
                )
            )
            await self.session.execute(delete(AgentIdentityRecord).where(AgentIdentityRecord.id.in_(identity_ids)))

    async def _propagate_derived_source_deletion(self, run_ids: list[str]) -> None:
        now = utc_now()
        (
            affected_sources,
            run_memory_ids,
            revoked_memory_ids,
        ) = await self._propagate_memory_source_deletion(run_ids, now)
        invalid_memory_ids = run_memory_ids | revoked_memory_ids
        await self._propagate_evolution_source_deletion(run_ids, invalid_memory_ids, now)
        removed_source_ids = {source.id for source in affected_sources}
        if affected_sources:
            await self.session.execute(delete(MemorySourceRecord).where(MemorySourceRecord.id.in_(removed_source_ids)))
        if run_memory_ids:
            await self._delete_run_memories(run_memory_ids)

    async def _propagate_memory_source_deletion(self, run_ids, now):
        affected_sources = list(
            (await self.session.scalars(select(MemorySourceRecord).where(MemorySourceRecord.run_id.in_(run_ids)))).all()
        )
        affected_memory_ids = {source.memory_id for source in affected_sources}
        affected_memory_ids.update(
            (
                await self.session.scalars(select(PersistedMemoryRecord.id).where(PersistedMemoryRecord.run_id.in_(run_ids)))
            ).all()
        )
        memories = (
            list(
                (
                    await self.session.scalars(
                        select(PersistedMemoryRecord).where(PersistedMemoryRecord.id.in_(affected_memory_ids))
                    )
                ).all()
            )
            if affected_memory_ids
            else []
        )
        all_memory_sources = (
            list(
                (
                    await self.session.scalars(
                        select(MemorySourceRecord).where(MemorySourceRecord.memory_id.in_(affected_memory_ids))
                    )
                ).all()
            )
            if affected_memory_ids
            else []
        )
        removed_source_ids = {source.id for source in affected_sources}
        remaining_by_memory: dict[str, list[MemorySourceRecord]] = {}
        for source in all_memory_sources:
            if source.id not in removed_source_ids and source.accessible and source.revoked_at is None:
                remaining_by_memory.setdefault(source.memory_id, []).append(source)

        run_memory_ids: set[str] = set()
        revoked_memory_ids: set[str] = set()
        for memory in memories:
            if _belongs_to_deleted_run(memory, run_ids):
                run_memory_ids.add(memory.id)
                continue
            remaining_sources = remaining_by_memory.get(memory.id, [])
            memory.run_id = None if memory.run_id in run_ids else memory.run_id
            memory.provenance = {
                "source_count": len(remaining_sources),
                "deleted_sources_redacted": True,
            }
            if not remaining_sources and memory.status in {"candidate", "active", "quarantined"}:
                memory.status = "revoked"
                memory.state_version += 1
                memory.valid_to = now
                memory.revoked_at = now
                memory.revoke_reason = "source_conversation_deleted"
                memory.updated_at = now
                revoked_memory_ids.add(memory.id)
                event_type = "revoked_by_source_deletion"
            else:
                event_type = "source_removed"
            self.session.add(
                MemoryAuditRecord(
                    memory_id=memory.id,
                    event_type=event_type,
                    actor="conversation-lifecycle",
                    reason="source_conversation_deleted",
                    payload={
                        "deleted_source_count": sum(source.memory_id == memory.id for source in affected_sources),
                        "remaining_source_count": len(remaining_sources),
                    },
                    created_at=now,
                )
            )

        return affected_sources, run_memory_ids, revoked_memory_ids

    async def _propagate_evolution_source_deletion(self, run_ids, invalid_memory_ids, now):
        evolution_sources = list(
            (
                await self.session.scalars(
                    select(AgentEvolutionSourceRecord).where(
                        or_(
                            AgentEvolutionSourceRecord.run_id.in_(run_ids),
                            AgentEvolutionSourceRecord.memory_id.in_(invalid_memory_ids),
                        )
                    )
                )
            ).all()
        )
        affected_candidate_ids = {source.candidate_id for source in evolution_sources}
        if affected_candidate_ids:
            removed_evolution_source_ids = {source.id for source in evolution_sources}
            all_evolution_sources = list(
                (
                    await self.session.scalars(
                        select(AgentEvolutionSourceRecord).where(
                            AgentEvolutionSourceRecord.candidate_id.in_(affected_candidate_ids)
                        )
                    )
                ).all()
            )
            remaining_by_candidate: dict[str, int] = {}
            for source in all_evolution_sources:
                if source.id not in removed_evolution_source_ids and source.accessible:
                    remaining_by_candidate[source.candidate_id] = remaining_by_candidate.get(source.candidate_id, 0) + 1
            candidates = list(
                (
                    await self.session.scalars(
                        select(AgentEvolutionCandidateRecord).where(
                            AgentEvolutionCandidateRecord.id.in_(affected_candidate_ids)
                        )
                    )
                ).all()
            )
            for candidate in candidates:
                remaining_count = remaining_by_candidate.get(candidate.id, 0)
                previous_status = candidate.status
                if remaining_count == 0:
                    candidate.status = "rolled_back" if candidate.status in {"shadow", "canary", "promoted"} else "rejected"
                    if candidate.status != previous_status:
                        candidate.state_version += 1
                    candidate.reviewed_by = "conversation-lifecycle"
                    candidate.review_reason = "source_conversation_deleted"
                    candidate.updated_at = now
                    event_type = "rejected_by_source_deletion"
                else:
                    event_type = "source_removed"
                self.session.add(
                    AgentEvolutionAuditRecord(
                        candidate_id=candidate.id,
                        event_type=event_type,
                        actor="conversation-lifecycle",
                        reason="source_conversation_deleted",
                        actual_state_version=candidate.state_version,
                        payload={
                            "previous_status": previous_status,
                            "current_status": candidate.status,
                            "remaining_source_count": remaining_count,
                        },
                        created_at=now,
                    )
                )
            await self.session.execute(
                delete(AgentEvolutionSourceRecord).where(AgentEvolutionSourceRecord.id.in_(removed_evolution_source_ids))
            )

    async def _delete_run_memories(self, run_memory_ids) -> None:
        await self.session.execute(
            delete(MemoryLinkRecord).where(
                MemoryLinkRecord.source_memory_id.in_(run_memory_ids) | MemoryLinkRecord.target_memory_id.in_(run_memory_ids)
            )
        )
        for model in (MemoryAuditRecord, MemorySourceRecord):
            await self.session.execute(delete(model).where(model.memory_id.in_(run_memory_ids)))
        await self.session.execute(delete(PersistedMemoryRecord).where(PersistedMemoryRecord.id.in_(run_memory_ids)))


def conversation_summary(task: TaskRecord) -> dict:
    runs = sorted(task.runs, key=lambda item: item.created_at)
    latest = runs[-1] if runs else None
    return {
        "id": task.id,
        "title": task.title,
        "title_source": task.title_source or "auto",
        "preferred_answer_mode": task.preferred_answer_mode or "standard",
        "pinned_at": task.pinned_at,
        "created_at": task.created_at,
        "updated_at": task.updated_at,
        "last_run_status": latest.status if latest else None,
        "last_message_preview": (latest.summary or latest.model_policy.get("conversation_goal", "")) if latest else "",
        "has_active_share": bool(task.share and task.share.active),
    }


def conversation_view(task: TaskRecord) -> dict:
    state = task.context_state if isinstance(task.context_state, dict) else {}
    command_messages = [
        item
        for item in state.get("command_history", [])
        if isinstance(item, dict) and item.get("id") and item.get("command") and item.get("created_at")
    ]
    return {
        **conversation_summary(task),
        "runs": [run_payload(run) for run in sorted(task.runs, key=lambda item: item.created_at)],
        "command_messages": command_messages,
    }
