from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.model_base import utc_now
from app.db.models.memory import MemoryRecord
from app.memory.domain import MemoryNamespace, MemoryStatus
from app.repositories.memory_time import as_utc


class MemoryQueryRepository:
    """Read-only Memory projections and version history."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def list_records(
        self,
        *,
        scope: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        namespaces: Iterable[MemoryNamespace] | None = None,
        statuses: Iterable[str | MemoryStatus] | None = None,
        min_confidence: float = 0.0,
        include_expired: bool = True,
        include_sources: bool = False,
        limit: int = 100,
    ) -> list[MemoryRecord]:
        query = select(MemoryRecord).where(MemoryRecord.confidence >= min_confidence)
        if include_sources:
            query = query.options(selectinload(MemoryRecord.sources))
        query = self._apply_filters(
            query,
            scope=scope,
            kind=kind,
            run_id=run_id,
            namespaces=list(namespaces or []),
            statuses=list(statuses or []),
            include_expired=include_expired,
        )
        query = query.order_by(MemoryRecord.updated_at.desc(), MemoryRecord.id).limit(limit)
        return list((await self.session.execute(query)).scalars().all())

    @staticmethod
    def _apply_filters(
        query,
        *,
        scope: str | None,
        kind: str | None,
        run_id: str | None,
        namespaces: list[MemoryNamespace],
        statuses: list[str | MemoryStatus],
        include_expired: bool,
    ):
        if scope:
            query = query.where(MemoryRecord.scope == scope)
        if kind:
            query = query.where(MemoryRecord.kind == kind)
        if run_id:
            query = query.where(MemoryRecord.run_id == run_id)
        if namespaces:
            query = query.where(or_(*[_namespace_filter(item) for item in namespaces]))
        if statuses:
            query = query.where(MemoryRecord.status.in_([_status_value(item) for item in statuses]))
        if not include_expired:
            now = utc_now()
            query = query.where(
                or_(MemoryRecord.expires_at.is_(None), MemoryRecord.expires_at > now),
                or_(MemoryRecord.valid_to.is_(None), MemoryRecord.valid_to > now),
            )
        return query

    async def history(
        self,
        *,
        namespace: MemoryNamespace,
        memory_key: str,
        as_of: datetime | None = None,
    ) -> list[MemoryRecord]:
        query = select(MemoryRecord).where(
            MemoryRecord.namespace_type == namespace.type.value,
            MemoryRecord.namespace_id == namespace.id,
            MemoryRecord.memory_key == memory_key,
        )
        if as_of is not None:
            instant = as_utc(as_of)
            query = query.where(
                MemoryRecord.valid_from <= instant,
                or_(MemoryRecord.valid_to.is_(None), MemoryRecord.valid_to > instant),
            )
        query = query.order_by(MemoryRecord.version)
        return list((await self.session.execute(query)).scalars().all())


def _namespace_filter(namespace: MemoryNamespace):
    return and_(
        MemoryRecord.namespace_type == namespace.type.value,
        MemoryRecord.namespace_id == namespace.id,
    )


def _status_value(status: str | MemoryStatus) -> str:
    if isinstance(status, MemoryStatus):
        return status.value
    return MemoryStatus(status).value
