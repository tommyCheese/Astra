import uuid
from dataclasses import dataclass
from typing import Any

from app.db.models.memory import MemoryRecord


@dataclass(frozen=True)
class MemoryCreateRequest:
    scope: str
    kind: str
    content: str
    provenance: dict[str, Any]
    confidence: float
    run_id: str | None
    created_by: str | None
    structured_data: dict[str, Any] | None
    memory_key: str | None
    status: str
    importance: float
    utility_score: float
    observed_at: Any
    valid_from: Any
    valid_to: Any
    expires_at: Any
    normalize_kind: bool


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
    ) -> MemoryRecord:
        request = MemoryCreateRequest(
            scope=scope,
            kind=kind,
            content=content,
            provenance=provenance,
            confidence=confidence,
            run_id=run_id,
            created_by=created_by,
            structured_data=structured_data,
            memory_key=memory_key,
            status=status,
            importance=importance,
            utility_score=utility_score,
            observed_at=observed_at,
            valid_from=valid_from,
            valid_to=valid_to,
            expires_at=expires_at,
            normalize_kind=normalize_kind,
        )
        memory = await self._create_memory_record(request)
        await self._record_memory_write(request, memory)
        await self.session.flush()
        return memory

    async def _create_memory_record(self, request: MemoryCreateRequest) -> MemoryRecord:
        from app.memory.domain import (
            MemoryNamespace,
            MemoryNamespaceType,
            MemoryValidationError,
        )
        from app.repositories.memories import MemoryRepository

        namespace = None
        if request.run_id is None:
            if request.scope == "user" and request.created_by:
                namespace = MemoryNamespace(MemoryNamespaceType.user, request.created_by)
            else:
                namespace = MemoryNamespace(MemoryNamespaceType.run, str(uuid.uuid4()))
        try:
            return await MemoryRepository(self.session).create(
                run_id=request.run_id,
                namespace=namespace,
                scope=request.scope,
                kind=request.kind,
                content=request.content,
                structured_data=request.structured_data,
                provenance=request.provenance,
                confidence=request.confidence,
                memory_key=request.memory_key,
                status=request.status,
                importance=request.importance,
                utility_score=request.utility_score,
                observed_at=request.observed_at,
                valid_from=request.valid_from,
                valid_to=request.valid_to,
                expires_at=request.expires_at,
                created_by=request.created_by,
                normalize_kind=request.normalize_kind,
                commit=False,
            )
        except MemoryValidationError as exc:
            if request.run_id:
                await self.add_event(
                    request.run_id,
                    "memory.write_rejected",
                    {
                        "scope": request.scope,
                        "kind": request.kind,
                        "reason": str(exc),
                    },
                )
                await self.session.flush()
            raise ValueError(str(exc)) from exc

    async def _record_memory_write(
        self,
        request: MemoryCreateRequest,
        memory: MemoryRecord,
    ) -> None:
        if request.run_id:
            await self.add_event(
                request.run_id,
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

    async def list_memories(
        self,
        *,
        scope: str | None = None,
        kind: str | None = None,
        run_id: str | None = None,
        min_confidence: float = 0.0,
        limit: int = 10,
    ) -> list[MemoryRecord]:
        from app.memory.domain import MemoryStatus
        from app.repositories.memory_queries import MemoryQueryRepository

        return await MemoryQueryRepository(self.session).list_records(
            scope=scope,
            kind=kind,
            run_id=run_id,
            statuses=[MemoryStatus.active],
            min_confidence=min_confidence,
            include_expired=False,
            limit=limit,
        )
