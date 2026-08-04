from __future__ import annotations

from datetime import datetime
from typing import Any

from app.db.models.memory import MemoryAuditRecord


def record_memory_audit(
    session: Any,
    memory_id: str,
    event_type: str,
    actor: str | None,
    reason: str | None,
    payload: dict[str, Any],
    created_at: datetime,
) -> None:
    session.add(
        MemoryAuditRecord(
            memory_id=memory_id,
            event_type=event_type,
            actor=actor,
            reason=reason,
            payload=payload,
            created_at=created_at,
        )
    )
