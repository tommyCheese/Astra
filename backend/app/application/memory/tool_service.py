from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from app.domain.memory import (
    TERMINAL_MEMORY_STATUSES,
    MemoryConflictError,
    MemoryNamespace,
    MemoryNamespaceType,
    MemoryStatus,
    MemoryValidationError,
)
from app.infrastructure.repositories.memories import MemoryRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork

PROTECTED_MEMORY_FIELDS = frozenset(
    {
        "approval",
        "approvals",
        "credential",
        "credentials",
        "permission",
        "permissions",
        "sandbox",
        "system_prompt",
        "tool_allowlist",
    }
)


class MemoryToolService:
    """Run-scoped, audited entry point for model-invoked Memory mutations."""

    def __init__(self, repository: RunUnitOfWork, *, writes_enabled: bool) -> None:
        self._run_repository = repository
        self._repository = MemoryRepository(repository.session)
        self._writes_enabled = writes_enabled

    async def remember(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        actor: str | None,
        content: str,
        scope: str,
        kind: str,
        memory_key: str | None,
        confidence: float,
        importance: float,
        structured_data: dict[str, Any],
        expires_in_days: int | None,
    ) -> dict[str, Any]:
        if not self._writes_enabled:
            raise MemoryValidationError("Memory writes are disabled by runtime settings")
        if PROTECTED_MEMORY_FIELDS & set(structured_data):
            raise MemoryValidationError("Memory cannot carry protected authority fields")
        normalized_content = str(content or "").strip()
        key = str(memory_key or "").strip() or self._derived_key(scope, kind, normalized_content)
        if len(key) > 240:
            raise MemoryValidationError("Memory key exceeds the 240 character limit")
        namespace, _ = await self._repository.namespace_for_write(run_id=run_id, scope=scope)
        existing = await self._repository.latest_for_key(
            namespace=namespace,
            memory_key=key,
            include_sources=True,
        )
        if existing:
            if existing.status in TERMINAL_MEMORY_STATUSES:
                raise MemoryConflictError("Memory key belongs to a terminal Memory version")
            if existing.content == normalized_content:
                await self._record_event(run_id, existing, "memory.remember_deduplicated")
                return self._view(existing, deduplicated=True)
        provenance = {"run_id": run_id, "tool_call_id": tool_call_id, "source": "remember"}
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days) if expires_in_days is not None else None
        if existing is not None:
            if existing.status != MemoryStatus.active.value:
                raise MemoryConflictError("Memory key already has a pending candidate")
            memory = await self._repository.create_candidate_version(
                existing.id,
                expected_state_version=existing.state_version,
                source_run_id=run_id,
                content=normalized_content,
                provenance=provenance,
                structured_data=structured_data,
                confidence=confidence,
                importance=importance,
                actor=actor or "remember-tool",
                reason="explicit remember request awaiting human activation",
            )
        else:
            memory = await self._repository.create(
                run_id=run_id,
                scope=scope,
                kind=kind,
                content=normalized_content,
                provenance=provenance,
                confidence=confidence,
                memory_key=key,
                status=MemoryStatus.candidate,
                structured_data=structured_data,
                importance=importance,
                expires_at=expires_at,
                created_by=actor or "remember-tool",
                normalize_kind=True,
                commit=False,
            )
        await self._record_event(run_id, memory, "memory.remembered")
        return self._view(memory, deduplicated=False)

    async def forget(
        self,
        *,
        run_id: str,
        actor: str | None,
        memory_id: str,
        reason: str,
    ) -> dict[str, Any]:
        memory = await self._repository.require(memory_id)
        allowed = await self._repository.namespaces_for_run(run_id)
        namespace = MemoryNamespace(MemoryNamespaceType(memory.namespace_type), memory.namespace_id)
        if namespace not in allowed:
            raise MemoryValidationError("Memory is outside the current Run namespace boundary")
        if memory.status in TERMINAL_MEMORY_STATUSES:
            return self._forget_view(memory, forgotten=False)
        revoked = await self._repository.transition(
            memory.id,
            MemoryStatus.revoked,
            expected_state_version=memory.state_version,
            actor=actor or "forget-tool",
            reason=reason,
            commit=False,
        )
        await self._run_repository.add_event(
            run_id,
            "memory.forgotten",
            {
                "memory_id": revoked.id,
                "memory_key": revoked.memory_key,
                "scope": revoked.scope,
                "reason": reason,
            },
        )
        await self._run_repository.session.commit()
        return self._forget_view(revoked, forgotten=True)

    async def _record_event(self, run_id: str, memory: Any, event_type: str) -> None:
        await self._run_repository.add_event(
            run_id,
            event_type,
            {
                "memory_id": memory.id,
                "memory_key": memory.memory_key,
                "scope": memory.scope,
                "kind": memory.kind,
                "status": memory.status,
                "version": memory.version,
            },
        )
        await self._run_repository.session.commit()

    @staticmethod
    def _derived_key(scope: str, kind: str, content: str) -> str:
        digest = hashlib.sha256(f"{scope}\0{kind}\0{content}".encode()).hexdigest()[:32]
        return f"remember:{kind}:{digest}"

    @staticmethod
    def _view(memory: Any, *, deduplicated: bool) -> dict[str, Any]:
        return {
            "memory_id": memory.id,
            "memory_key": memory.memory_key,
            "scope": memory.scope,
            "kind": memory.kind,
            "status": memory.status,
            "version": memory.version,
            "state_version": memory.state_version,
            "deduplicated": deduplicated,
        }

    @staticmethod
    def _forget_view(memory: Any, *, forgotten: bool) -> dict[str, Any]:
        return {
            "memory_id": memory.id,
            "memory_key": memory.memory_key,
            "status": memory.status,
            "state_version": memory.state_version,
            "forgotten": forgotten,
        }
