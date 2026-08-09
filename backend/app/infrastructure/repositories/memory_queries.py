from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memory import MemoryNamespace, MemoryStatus
from app.infrastructure.db.model_base import as_utc, utc_now
from app.infrastructure.db.models.memory import PersistedMemoryRecord


@dataclass
class MemoryQueryRepository:
    """Read-only Memory projections and version history."""

    session: AsyncSession

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
    ) -> list[PersistedMemoryRecord]:
        query = select(PersistedMemoryRecord).where(PersistedMemoryRecord.confidence >= min_confidence)
        if include_sources:
            query = query.options(selectinload(PersistedMemoryRecord.sources))
        query = self._apply_filters(
            query,
            scope=scope,
            kind=kind,
            run_id=run_id,
            namespaces=list(namespaces or []),
            statuses=list(statuses or []),
            include_expired=include_expired,
        )
        query = query.order_by(PersistedMemoryRecord.updated_at.desc(), PersistedMemoryRecord.id).limit(limit)
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
            query = query.where(PersistedMemoryRecord.scope == scope)
        if kind:
            query = query.where(PersistedMemoryRecord.kind == kind)
        if run_id:
            query = query.where(PersistedMemoryRecord.run_id == run_id)
        if namespaces:
            query = query.where(or_(*[_namespace_filter(item) for item in namespaces]))
        if statuses:
            query = query.where(PersistedMemoryRecord.status.in_([_status_value(item) for item in statuses]))
        if not include_expired:
            now = utc_now()
            query = query.where(
                or_(PersistedMemoryRecord.expires_at.is_(None), PersistedMemoryRecord.expires_at > now),
                or_(PersistedMemoryRecord.valid_to.is_(None), PersistedMemoryRecord.valid_to > now),
            )
        return query

    async def history(
        self,
        *,
        namespace: MemoryNamespace,
        memory_key: str,
        as_of: datetime | None = None,
    ) -> list[PersistedMemoryRecord]:
        query = select(PersistedMemoryRecord).where(
            PersistedMemoryRecord.namespace_type == namespace.type.value,
            PersistedMemoryRecord.namespace_id == namespace.id,
            PersistedMemoryRecord.memory_key == memory_key,
        )
        if as_of is not None:
            instant = as_utc(as_of)
            query = query.where(
                PersistedMemoryRecord.valid_from <= instant,
                or_(PersistedMemoryRecord.valid_to.is_(None), PersistedMemoryRecord.valid_to > instant),
            )
        query = query.order_by(PersistedMemoryRecord.version)
        return list((await self.session.execute(query)).scalars().all())


def _namespace_filter(namespace: MemoryNamespace):
    return and_(
        PersistedMemoryRecord.namespace_type == namespace.type.value,
        PersistedMemoryRecord.namespace_id == namespace.id,
    )


def _status_value(status: str | MemoryStatus) -> str:
    if isinstance(status, MemoryStatus):
        return status.value
    return MemoryStatus(status).value
