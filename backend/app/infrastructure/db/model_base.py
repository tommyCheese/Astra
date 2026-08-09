"""Shared SQLAlchemy registry and persistence primitives."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, TypeVar, overload

from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import JSON


class AstraOrmRecordBase(DeclarativeBase):
    pass


JsonType = JSON().with_variant(JSONB, "postgresql")
RecordT = TypeVar("RecordT")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@overload
def as_utc(value: datetime) -> datetime: ...


@overload
def as_utc(value: None) -> None: ...


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def uuid_str() -> str:
    return str(uuid.uuid4())


async def require_record(
    session: AsyncSession,
    model: type[RecordT],
    record_id: Any,
    label: str,
) -> RecordT:
    record = await session.get(model, record_id)
    if record is None:
        raise ValueError(f"{label} not found: {record_id}")
    return record
