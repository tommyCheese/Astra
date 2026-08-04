from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.model_base import utc_now
from app.db.models.memory import MemoryRecord
from app.memory.domain import MemoryStatus
from app.repositories.memory_audit import record_memory_audit
from app.repositories.memory_time import as_utc


class MemoryRetentionRepository:
    """Materializes time-based Memory lifecycle transitions."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def materialize_expired(
        self,
        *,
        as_of: datetime | None = None,
        limit: int = 500,
        commit: bool = True,
    ) -> int:
        instant = as_utc(as_of) or utc_now()
        records = list(
            (
                await self.session.execute(
                    select(MemoryRecord)
                    .where(
                        MemoryRecord.status == MemoryStatus.active.value,
                        MemoryRecord.expires_at.is_not(None),
                        MemoryRecord.expires_at <= instant,
                    )
                    .order_by(MemoryRecord.expires_at, MemoryRecord.id)
                    .limit(max(0, limit))
                )
            )
            .scalars()
            .all()
        )
        materialized = 0
        for memory in records:
            if await self._expire(memory, instant):
                materialized += 1
        await self.session.flush()
        if commit:
            await self.session.commit()
        return materialized

    async def _expire(self, memory: MemoryRecord, instant: datetime) -> bool:
        result = await self.session.execute(
            update(MemoryRecord)
            .where(
                MemoryRecord.id == memory.id,
                MemoryRecord.status == MemoryStatus.active.value,
                MemoryRecord.state_version == memory.state_version,
            )
            .values(
                status=MemoryStatus.expired.value,
                state_version=memory.state_version + 1,
                valid_to=instant,
                updated_at=instant,
            )
        )
        if result.rowcount != 1:
            return False
        record_memory_audit(
            self.session,
            memory.id,
            "expiration_materialized",
            "memory-retention",
            "expires_at_elapsed",
            {"expires_at": memory.expires_at.isoformat() if memory.expires_at else None},
            instant,
        )
        return True
