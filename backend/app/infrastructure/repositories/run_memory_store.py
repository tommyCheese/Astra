import uuid
from typing import Any

from app.domain.memory import (
    MemoryNamespace,
    MemoryNamespaceType,
    MemoryStatus,
    MemoryValidationError,
)
from app.infrastructure.db.models.memory import PersistedMemoryRecord
from app.infrastructure.repositories.memories import MemoryRepository
from app.infrastructure.repositories.memory_queries import MemoryQueryRepository


class RunMemoryStore:
    async def create_memory(
        self,
        *,
        scope: str,
        kind: str,
        content: str,
        provenance: dict[str, Any],
        confidence: float,
        run_id: str | None = None,
        created_by: str | None = None,
        structured_data: dict[str, Any] | None = None,
        memory_key: str | None = None,
        status: str = "active",
        importance: float = 0.5,
        utility_score: float = 0.0,
        observed_at=None,
        valid_from=None,
        valid_to=None,
        expires_at=None,
        normalize_kind: bool = False,
    ) -> PersistedMemoryRecord:
        namespace = None
        if run_id is None:
            namespace = (
                MemoryNamespace(MemoryNamespaceType.user, created_by)
                if scope == "user" and created_by
                else MemoryNamespace(MemoryNamespaceType.run, str(uuid.uuid4()))
            )
        try:
            memory = await MemoryRepository(self.session).create(
                run_id=run_id,
                namespace=namespace,
                scope=scope,
                kind=kind,
                content=content,
                structured_data=structured_data,
                provenance=provenance,
                confidence=confidence,
                memory_key=memory_key,
                status=status,
                importance=importance,
                utility_score=utility_score,
                observed_at=observed_at,
                valid_from=valid_from,
                valid_to=valid_to,
                expires_at=expires_at,
                created_by=created_by,
                normalize_kind=normalize_kind,
                commit=False,
            )
        except MemoryValidationError as exc:
            if run_id:
                await self.add_event(
                    run_id,
                    "memory.write_rejected",
                    {"scope": scope, "kind": kind, "reason": str(exc)},
                )
                await self.session.flush()
            raise ValueError(str(exc)) from exc
        if run_id:
            await self.add_event(
                run_id,
                "memory.write",
                {
                    "memory_id": memory.id,
                    "scope": memory.scope,
                    "kind": memory.kind,
                    "status": memory.status,
                    "memory_key": memory.memory_key,
                    "version": memory.version,
                    "confidence": memory.confidence,
                    "provenance": memory.provenance,
                },
            )
        await self.session.flush()
        return memory

    async def list_memories(
        self,
        *,
        scope: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[PersistedMemoryRecord]:
        return await MemoryQueryRepository(self.session).list_records(
            scope=scope,
            kind=kind,
            run_id=run_id,
            statuses=[MemoryStatus.active],
            min_confidence=min_confidence,
            include_expired=False,
            limit=limit,
        )
