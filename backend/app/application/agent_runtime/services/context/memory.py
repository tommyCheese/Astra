"""Memory retrieval and bounded model-context projection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from app.application.memory.retrieval import (
    MemoryRetrievalBudget,
    MemoryRetrievalCandidate,
    MemoryRetrievalPolicy,
    MemoryRetrievalQuery,
    ScoredMemory,
    retrieve_memories,
)
from app.common.core.config import AstraRuntimeSettings
from app.infrastructure.repositories.memories import MemoryRepository
from app.infrastructure.repositories.memory_queries import MemoryQueryRepository
from app.infrastructure.repositories.memory_recall import MemoryRecallRepository
from app.infrastructure.repositories.run_unit_of_work import RunUnitOfWork


@dataclass(frozen=True)
class AgentMemoryContext:
    audit_reads: list[dict[str, Any]]
    context_reads: list[dict[str, Any]]
    recall_event_id: str | None


@dataclass
class MemoryContextReader:
    _run_repository: RunUnitOfWork
    _settings: AstraRuntimeSettings | None

    async def project(
        self,
        run_id: str,
        goal: str,
        memories: list[Any],
    ) -> AgentMemoryContext:
        scored_by_id: dict[str, dict[str, float | None]] = {}
        recall_event_id = None
        if self._settings and self._settings.agent_memory_cross_session_enabled:
            selected, recall_event_id = await self._retrieve(run_id, goal)
            memories = [scored.candidate for scored in selected]
            scored_by_id = {scored.candidate.id: scored.score.as_dict() for scored in selected}
        return AgentMemoryContext(
            audit_reads=[self._audit_view(memory, scored_by_id.get(memory.id), recall_event_id) for memory in memories],
            context_reads=[self._context_view(memory, scored_by_id.get(memory.id)) for memory in memories],
            recall_event_id=recall_event_id,
        )

    async def _retrieve(self, run_id: str, goal: str) -> tuple[list[ScoredMemory], str]:
        assert self._settings is not None
        repository = MemoryRepository(self._run_repository.session)
        namespaces = await repository.namespaces_for_run(run_id)
        records = await MemoryQueryRepository(self._run_repository.session).list_records(
            namespaces=namespaces,
            min_confidence=0.0,
            include_expired=True,
            include_sources=True,
            limit=self._settings.agent_memory_retrieval_candidate_limit,
        )
        candidates = [self._candidate(record) for record in records]
        retrieval = retrieve_memories(
            candidates,
            MemoryRetrievalQuery(
                text=goal,
                namespaces=frozenset(namespaces),
                as_of=datetime.now(timezone.utc),
            ),
            policy=MemoryRetrievalPolicy(
                minimum_confidence=self._settings.agent_memory_retrieval_min_confidence,
                minimum_score=self._settings.agent_memory_retrieval_min_score,
            ),
            budget=MemoryRetrievalBudget(
                max_items=self._settings.agent_memory_retrieval_max_items,
                max_characters=self._settings.agent_memory_retrieval_max_characters,
                max_tokens=self._settings.agent_memory_retrieval_max_tokens,
            ),
        )
        event = await self._record_recall(
            MemoryRecallRepository(self._run_repository.session),
            run_id,
            goal,
            namespaces,
            candidates,
            retrieval,
        )
        return list(retrieval.selected), event.id

    @staticmethod
    def _candidate(memory: Any) -> MemoryRetrievalCandidate:
        return MemoryRetrievalCandidate(
            id=memory.id,
            namespace_type=memory.namespace_type,
            namespace_id=memory.namespace_id,
            kind=memory.kind,
            status=memory.status,
            content=memory.content,
            structured_data=memory.structured_data or {},
            provenance=memory.provenance or {},
            confidence=memory.confidence,
            importance=memory.importance,
            utility_score=memory.utility_score,
            version=memory.version,
            observed_at=memory.observed_at,
            valid_from=memory.valid_from,
            valid_to=memory.valid_to,
            expires_at=memory.expires_at,
            revoked_at=memory.revoked_at,
            updated_at=memory.updated_at,
            accessible_source_count=sum(source.accessible and source.revoked_at is None for source in memory.sources),
        )

    async def _record_recall(
        self,
        repository: MemoryRecallRepository,
        run_id: str,
        goal: str,
        namespaces: set[Any],
        candidates: list[MemoryRetrievalCandidate],
        retrieval: Any,
    ) -> Any:
        assert self._settings is not None
        manifest = [namespace.as_dict() for namespace in sorted(namespaces, key=lambda value: (value.type.value, value.id))]
        query_hash = self._query_hash(goal, manifest)
        ranked = {item.candidate.id: item for item in retrieval.ranked}
        return await repository.record_event(
            run_id=run_id,
            query_hash=query_hash,
            policy_version=self._settings.agent_memory_retrieval_policy_version,
            namespace_manifest=manifest,
            candidates=[
                {
                    "id": memory.id,
                    "version": memory.version,
                    "namespace_type": memory.namespace_type,
                    "namespace_id": memory.namespace_id,
                    "status": memory.status,
                    "score": ranked[memory.id].score.as_dict() if memory.id in ranked else None,
                }
                for memory in candidates
            ],
            selected=[
                {
                    "id": item.candidate.id,
                    "version": item.candidate.version,
                    "score": item.score.as_dict(),
                }
                for item in retrieval.selected
            ],
            excluded=[
                {"id": item.memory_id, "stage": item.stage, "reasons": list(item.reasons)} for item in retrieval.excluded
            ],
        )

    def _query_hash(self, goal: str, namespace_manifest: list[dict[str, Any]]) -> str:
        assert self._settings is not None
        return hashlib.sha256(
            json.dumps(
                {
                    "query": goal,
                    "namespaces": namespace_manifest,
                    "policy_version": self._settings.agent_memory_retrieval_policy_version,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    @staticmethod
    def _audit_view(
        memory: Any,
        score: dict[str, float | None] | None,
        recall_event_id: str | None,
    ) -> dict[str, Any]:
        view = {
            "id": memory.id,
            "memory_key": getattr(memory, "memory_key", None),
            "namespace_type": getattr(memory, "namespace_type", None),
            "namespace_id": getattr(memory, "namespace_id", None),
            "scope": getattr(memory, "scope", getattr(memory, "namespace_type", "run")),
            "kind": memory.kind,
            "status": getattr(memory, "status", "active"),
            "version": getattr(memory, "version", 1),
            "state_version": getattr(memory, "state_version", 1),
            "confidence": memory.confidence,
            "importance": getattr(memory, "importance", 0.5),
        }
        if score is not None:
            view["score"] = score
        if recall_event_id is not None:
            view["recall_event_id"] = recall_event_id
        return view

    @classmethod
    def _context_view(
        cls,
        memory: Any,
        score: dict[str, float | None] | None,
    ) -> dict[str, Any]:
        view = cls._audit_view(memory, score, None)
        view.update(
            content=memory.content,
            structured_data=getattr(memory, "structured_data", {}) or {},
            provenance=memory.provenance,
            trust="untrusted_memory_data",
            authority="none",
        )
        return view
