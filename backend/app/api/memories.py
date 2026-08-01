from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ResourceError, StateError, ValidationError
from app.db.models import MemoryAuditRecord, MemoryRecallEventRecord
from app.db.session import get_session
from app.memory.domain import (
    MemoryConflictError,
    MemoryNamespace,
    MemoryNamespaceType,
    MemoryStatus,
    MemoryValidationError,
)
from app.repositories.memories import MemoryRepository
from app.schemas.memory import (
    MemoryDetailView,
    MemoryListView,
    MemoryRecallFeedbackRequest,
    MemoryRecallView,
    MemoryRevocationRequest,
    MemoryView,
)

router = APIRouter(prefix="/api/memories", tags=["memories"])
recall_router = APIRouter(prefix="/api/memory-recalls", tags=["memories"])


def _memory_view(memory) -> MemoryView:
    return MemoryView.model_validate(memory)


def _recall_view(event: MemoryRecallEventRecord, memory_id: str) -> MemoryRecallView | None:
    selected = next(
        (item for item in event.selected or [] if item.get("id") == memory_id),
        None,
    )
    excluded = next(
        (item for item in event.excluded or [] if item.get("id") == memory_id),
        None,
    )
    candidate = next(
        (item for item in event.candidates or [] if item.get("id") == memory_id),
        None,
    )
    if selected is None and excluded is None and candidate is None:
        return None
    reasons = excluded.get("reasons") if excluded else None
    return MemoryRecallView(
        event_id=event.id,
        run_id=event.run_id,
        turn_id=event.turn_id,
        query_fingerprint=event.query_hash,
        policy_version=event.policy_version,
        selected=selected is not None,
        exclusion_reason=", ".join(str(item) for item in reasons)
        if isinstance(reasons, list)
        else None,
        scores=(selected or candidate or {}).get("score") or {},
        feedback=event.feedback or {},
        created_at=event.created_at,
    )


async def _require_memory(
    repository: MemoryRepository,
    memory_id: str,
    *,
    include_sources: bool = False,
):
    try:
        return await repository.require(memory_id, include_sources=include_sources)
    except MemoryValidationError as exc:
        raise ResourceError("MEMORY_NOT_FOUND", "找不到指定记忆。") from exc


async def _memory_detail(
    session: AsyncSession,
    repository: MemoryRepository,
    memory_id: str,
) -> MemoryDetailView:
    memory = await _require_memory(repository, memory_id, include_sources=True)
    namespace = MemoryNamespace(
        MemoryNamespaceType(memory.namespace_type),
        memory.namespace_id,
    )
    history = await repository.history(
        namespace=namespace,
        memory_key=memory.memory_key,
    )
    audits = list(
        (
            await session.execute(
                select(MemoryAuditRecord)
                .where(MemoryAuditRecord.memory_id == memory.id)
                .order_by(MemoryAuditRecord.created_at, MemoryAuditRecord.id)
            )
        )
        .scalars()
        .all()
    )
    recall_candidates = list(
        (
            await session.execute(
                select(MemoryRecallEventRecord)
                .order_by(MemoryRecallEventRecord.created_at.desc())
                .limit(1_000)
            )
        )
        .scalars()
        .all()
    )
    recalls = [
        recall
        for event in recall_candidates
        if (recall := _recall_view(event, memory.id)) is not None
    ]
    return MemoryDetailView(
        **_memory_view(memory).model_dump(),
        sources=memory.sources,
        recall_events=recalls,
        audit_events=audits,
        history=[_memory_view(item) for item in history],
    )


@router.get("", response_model=MemoryListView)
async def list_memories(
    query: str | None = Query(default=None, max_length=1_000),
    status: str | None = Query(default=None, max_length=40),
    kind: str | None = Query(default=None, max_length=80),
    namespace_type: str | None = Query(default=None, max_length=40),
    namespace_id: str | None = Query(default=None, max_length=120),
    run_id: str | None = Query(default=None, max_length=36),
    include_history: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
):
    if bool(namespace_type) != bool(namespace_id):
        raise ValidationError(
            "MEMORY_NAMESPACE_INCOMPLETE",
            "namespace_type 与 namespace_id 必须同时提供。",
        )
    namespaces = None
    if namespace_type and namespace_id:
        try:
            namespaces = [MemoryNamespace(MemoryNamespaceType(namespace_type), namespace_id)]
        except ValueError as exc:
            raise ValidationError(
                "MEMORY_NAMESPACE_INVALID",
                "记忆命名空间无效。",
            ) from exc
    statuses = None
    if status:
        try:
            statuses = [MemoryStatus(status)]
        except ValueError as exc:
            raise ValidationError("MEMORY_STATUS_INVALID", "记忆生命周期状态无效。") from exc
    elif not include_history:
        statuses = [MemoryStatus.active]
    records = await MemoryRepository(session).list_records(
        kind=kind,
        run_id=run_id,
        namespaces=namespaces,
        statuses=statuses,
        include_expired=include_history,
        limit=min(500, max(limit, limit * 4 if query else limit)),
    )
    if query:
        normalized_query = query.casefold().strip()
        records = [
            memory
            for memory in records
            if normalized_query in memory.content.casefold()
            or normalized_query in memory.memory_key.casefold()
        ]
    records = records[:limit]
    return MemoryListView(
        items=[_memory_view(memory) for memory in records],
        total=len(records),
        next_cursor=None,
    )


@router.get("/{memory_id}", response_model=MemoryDetailView)
async def get_memory(
    memory_id: str,
    session: AsyncSession = Depends(get_session),
):
    return await _memory_detail(session, MemoryRepository(session), memory_id)


@router.post("/{memory_id}/revoke", response_model=MemoryDetailView)
async def revoke_memory(
    memory_id: str,
    payload: MemoryRevocationRequest,
    session: AsyncSession = Depends(get_session),
):
    repository = MemoryRepository(session)
    await _require_memory(repository, memory_id)
    try:
        await repository.transition(
            memory_id,
            MemoryStatus.revoked,
            expected_state_version=payload.expected_state_version,
            actor=payload.actor,
            reason=payload.reason.strip(),
        )
    except MemoryConflictError as exc:
        raise StateError(
            "MEMORY_VERSION_CONFLICT",
            "记忆已被其他操作修改，请刷新后重试。",
        ) from exc
    except (MemoryValidationError, ValueError) as exc:
        raise StateError(
            "MEMORY_TRANSITION_INVALID",
            "当前记忆状态不允许撤销。",
        ) from exc
    return await _memory_detail(session, repository, memory_id)


@recall_router.post("/{recall_event_id}/feedback")
async def record_memory_recall_feedback(
    recall_event_id: str,
    payload: MemoryRecallFeedbackRequest,
    session: AsyncSession = Depends(get_session),
):
    try:
        event = await MemoryRepository(session).record_recall_feedback(
            recall_event_id,
            outcome=payload.outcome,
            utility_delta=payload.utility_delta,
            details=payload.details,
        )
    except MemoryValidationError as exc:
        if "not found" in str(exc):
            raise ResourceError(
                "MEMORY_RECALL_NOT_FOUND",
                "找不到指定召回事件。",
            ) from exc
        raise ValidationError(
            "MEMORY_RECALL_FEEDBACK_INVALID",
            "召回反馈参数无效。",
        ) from exc
    return {"event_id": event.id, "feedback": event.feedback}
