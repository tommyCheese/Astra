"""Validate and persist model-proposed Memory candidates."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from app.memory.domain import MemoryConflictError, MemoryStatus, MemoryValidationError
from app.model_clients.contracts import ModelClient, ModelOutputError
from app.repositories.memories import MemoryRepository
from app.repositories.run_unit_of_work import RunUnitOfWork

logger = logging.getLogger("astra.memory_candidates")
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


class MemoryCandidateWriter:
    def __init__(self, settings, repository: RunUnitOfWork, model_client: ModelClient) -> None:
        self._settings = settings
        self._run_repository = repository
        self._model_client = model_client
        self._memory_repository = MemoryRepository(repository.session)

    async def write_candidates(
        self, *, run_id: str, goal: str, context: dict[str, Any]
    ) -> list[dict[str, Any]]:
        if not self._settings.agent_memory_write_enabled:
            return []
        candidates = await self._extract(run_id, goal, context)
        memory_views = []
        for candidate in candidates:
            try:
                memory = await self._write_candidate(run_id, candidate)
            except (
                MemoryValidationError,
                MemoryConflictError,
                SQLAlchemyError,
                ValueError,
            ) as error:
                await self._reject(run_id, candidate, error)
                continue
            memory_views.append(self._memory_view(memory))
        return memory_views

    async def _extract(self, run_id: str, goal: str, context: dict[str, Any]) -> list[Any]:
        try:
            return await self._model_client.extract_memory_candidates(goal, context)
        except ModelOutputError as error:
            logger.warning("memory.extraction.skipped run_id=%s reason=%s", run_id, str(error))
            await self._run_repository.add_event(
                run_id, "memory.extraction_skipped", {"reason": "invalid_model_output"}
            )
            await self._run_repository.session.commit()
            return []

    async def _write_candidate(self, run_id: str, candidate: Any) -> Any:
        self._validate_candidate(candidate)
        namespace, _ = await self._memory_repository.namespace_for_write(
            run_id=run_id, scope=candidate.scope
        )
        provenance = {**dict(candidate.provenance), "run_id": run_id}
        memory_key = str(candidate.memory_key or "").strip()
        existing = await self._memory_repository.latest_for_key(
            namespace=namespace, memory_key=memory_key, include_sources=True
        )
        if existing and existing.status in {
            MemoryStatus.candidate.value,
            MemoryStatus.active.value,
        }:
            if existing.content == candidate.content:
                return await self._deduplicate(run_id, existing)
            if existing.status == MemoryStatus.active.value:
                return await self._create_version(run_id, existing, candidate, provenance)
            raise MemoryConflictError("Stable Memory key is not currently eligible for replacement")
        return await self._create_new(run_id, candidate, provenance, memory_key)

    @staticmethod
    def _validate_candidate(candidate: Any) -> None:
        if PROTECTED_MEMORY_FIELDS & set(candidate.structured_data):
            raise MemoryValidationError("Memory candidate cannot carry protected authority fields")
        if not str(candidate.memory_key or "").strip():
            raise MemoryValidationError("Memory candidate requires a stable key")

    async def _deduplicate(self, run_id: str, memory: Any) -> Any:
        await self._run_repository.add_event(
            run_id,
            "memory.write_deduplicated",
            {"memory_id": memory.id, "memory_key": memory.memory_key, "version": memory.version},
        )
        await self._run_repository.session.commit()
        return memory

    async def _create_version(
        self, run_id: str, existing: Any, candidate: Any, provenance: dict[str, Any]
    ) -> Any:
        memory = await self._memory_repository.create_candidate_version(
            existing.id,
            expected_state_version=existing.state_version,
            source_run_id=run_id,
            content=candidate.content,
            provenance=provenance,
            structured_data=candidate.structured_data,
            confidence=candidate.confidence,
            importance=candidate.importance,
            valid_from=candidate.valid_from,
            actor="memory-extractor",
            reason="new supported observation awaiting human activation",
        )
        await self._run_repository.add_event(
            run_id,
            "memory.candidate_created",
            {
                "memory_id": memory.id,
                "memory_key": memory.memory_key,
                "version": memory.version,
                "supersedes_id": memory.supersedes_id,
            },
        )
        await self._run_repository.session.commit()
        return memory

    async def _create_new(
        self, run_id: str, candidate: Any, provenance: dict[str, Any], memory_key: str
    ) -> Any:
        memory = await self._memory_repository.create(
            run_id=run_id,
            scope=candidate.scope,
            kind=candidate.kind,
            content=candidate.content,
            structured_data=candidate.structured_data,
            provenance=provenance,
            confidence=candidate.confidence,
            memory_key=memory_key,
            status=MemoryStatus.candidate,
            importance=candidate.importance,
            utility_score=0.0,
            observed_at=candidate.observed_at,
            valid_from=candidate.valid_from,
            valid_to=candidate.valid_to,
            expires_at=candidate.expires_at,
            normalize_kind=True,
            commit=False,
        )
        await self._run_repository.add_event(
            run_id,
            "memory.candidate_created",
            {
                "memory_id": memory.id,
                "memory_key": memory.memory_key,
                "scope": memory.scope,
                "kind": memory.kind,
            },
        )
        await self._run_repository.session.commit()
        return memory

    async def _reject(self, run_id: str, candidate: Any, error: Exception) -> None:
        await self._run_repository.session.rollback()
        logger.warning(
            "memory.candidate.rejected run_id=%s kind=%s reason=%s",
            run_id,
            candidate.kind,
            type(error).__name__,
        )
        await self._run_repository.add_event(
            run_id,
            "memory.write_rejected",
            {"scope": candidate.scope, "kind": candidate.kind, "reason": type(error).__name__},
        )
        await self._run_repository.session.commit()

    @staticmethod
    def _memory_view(memory: Any) -> dict[str, Any]:
        return {
            "id": memory.id,
            "memory_key": memory.memory_key,
            "namespace_type": memory.namespace_type,
            "namespace_id": memory.namespace_id,
            "scope": memory.scope,
            "kind": memory.kind,
            "status": memory.status,
            "version": memory.version,
            "state_version": memory.state_version,
            "confidence": memory.confidence,
            "importance": memory.importance,
        }
