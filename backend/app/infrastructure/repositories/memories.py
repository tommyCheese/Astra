from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.domain.memory import (
    MemoryConflictError,
    MemoryNamespace,
    MemoryNamespaceType,
    MemoryStatus,
    MemoryValidationError,
    normalize_memory_kind,
    validate_memory_transition,
)
from app.infrastructure.db.model_base import utc_now, uuid_str
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.memory import (
    MemoryAuditRecord,
    MemoryLinkRecord,
    MemorySourceRecord,
    PersistedMemoryRecord,
)
from app.infrastructure.db.models.permissions import ToolCallRecord
from app.infrastructure.db.models.runs import AgentTurnRecord, RunRecord
from app.infrastructure.db.models.workspaces import ArtifactRecord


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _copied_memory_source(source, memory_id: str, now: datetime) -> MemorySourceRecord:
    return MemorySourceRecord(
        memory_id=memory_id,
        source_kind=source.source_kind,
        source_ref=source.source_ref,
        source_hash=source.source_hash,
        run_id=source.run_id,
        turn_id=source.turn_id,
        tool_call_id=source.tool_call_id,
        artifact_id=source.artifact_id,
        source_data=source.source_data,
        accessible=True,
        created_at=now,
    )


def _memory_source_from_spec(spec, memory_id: str, now: datetime) -> MemorySourceRecord:
    return MemorySourceRecord(
        memory_id=memory_id,
        source_kind=spec["source_kind"],
        source_ref=spec["source_ref"],
        source_hash=_canonical_digest(spec),
        run_id=spec.get("run_id"),
        turn_id=spec.get("turn_id"),
        tool_call_id=spec.get("tool_call_id"),
        artifact_id=spec.get("artifact_id"),
        source_data=spec["source_data"],
        accessible=True,
        created_at=now,
    )


def _validate_new_memory(content, confidence, importance, utility_score, status, scope, provenance) -> MemoryStatus:
    if not content:
        raise MemoryValidationError("Memory content must be non-empty")
    if len(content) > 50_000:
        raise MemoryValidationError("Memory content exceeds the 50000 character limit")
    score_ranges = (
        (confidence, 0.0, 1.0, "confidence"),
        (importance, 0.0, 1.0, "importance"),
        (utility_score, -1.0, 1.0, "utility"),
    )
    for value, minimum, maximum, label in score_ranges:
        if not minimum <= value <= maximum:
            raise MemoryValidationError(f"Memory {label} must be between {minimum:g} and {maximum:g}")
    try:
        initial_status = MemoryStatus(status)
    except ValueError as exc:
        raise MemoryValidationError(f"Unsupported Memory status: {status}") from exc
    if initial_status not in {MemoryStatus.candidate, MemoryStatus.active}:
        raise MemoryValidationError("New Memory must start in candidate or active state")
    if scope in {"task", "session", "user"} and not provenance:
        raise MemoryValidationError("Persistent Memory requires provenance")
    return initial_status


def _require_persistent_sources(scope: str, source_specs: list[dict[str, Any]]) -> None:
    if scope in {"task", "session", "user"} and not source_specs:
        raise MemoryValidationError("Persistent Memory requires a valid source reference")


@dataclass
class MemoryRepository:
    session: AsyncSession

    async def _run_identity(self, run_id: str) -> tuple[RunRecord, TaskRecord]:
        row = (
            await self.session.execute(
                select(RunRecord, TaskRecord).join(TaskRecord, TaskRecord.id == RunRecord.task_id).where(RunRecord.id == run_id)
            )
        ).one_or_none()
        if row is None:
            raise MemoryValidationError(f"Run not found: {run_id}")
        return row[0], row[1]

    async def namespaces_for_run(self, run_id: str) -> list[MemoryNamespace]:
        run, task = await self._run_identity(run_id)
        namespaces = [
            MemoryNamespace(MemoryNamespaceType.run, run.id),
            MemoryNamespace(MemoryNamespaceType.task, task.id),
        ]
        if run.memory_session_id and run.memory_session_id.strip():
            namespaces.append(MemoryNamespace(MemoryNamespaceType.session, run.memory_session_id))
        if task.created_by and task.created_by.strip():
            namespaces.append(MemoryNamespace(MemoryNamespaceType.user, task.created_by))
        return namespaces

    async def namespace_for_write(
        self,
        *,
        run_id: str,
        scope: str,
    ) -> tuple[MemoryNamespace, TaskRecord]:
        run, task = await self._run_identity(run_id)
        if scope == "run":
            return MemoryNamespace(MemoryNamespaceType.run, run.id), task
        if scope == "task":
            return MemoryNamespace(MemoryNamespaceType.task, task.id), task
        if scope == "session":
            if not run.memory_session_id or not run.memory_session_id.strip():
                raise MemoryValidationError("Session Memory requires a non-empty Run session identity")
            return (
                MemoryNamespace(MemoryNamespaceType.session, run.memory_session_id),
                task,
            )
        if scope == "user":
            if not task.created_by or not task.created_by.strip():
                raise MemoryValidationError("User Memory requires a non-empty Task creator identity")
            return MemoryNamespace(MemoryNamespaceType.user, task.created_by), task
        raise MemoryValidationError(f"Unsupported Memory scope: {scope}")

    async def _validate_reference(
        self,
        model,
        reference_id: str,
        *,
        run_id: str,
        label: str,
    ) -> None:
        owner_run_id = await self.session.scalar(select(model.run_id).where(model.id == reference_id))
        if owner_run_id is None:
            raise MemoryValidationError(f"Memory provenance {label} not found: {reference_id}")
        if owner_run_id != run_id:
            raise MemoryValidationError(f"Memory provenance {label} belongs to another Run")

    async def _source_specs(
        self,
        *,
        run_id: str | None,
        provenance: dict[str, Any],
    ) -> list[dict[str, Any]]:
        specs: list[dict[str, Any]] = []
        if run_id is not None:
            claimed_run_id = provenance.get("run_id")
            if claimed_run_id is not None and claimed_run_id != run_id:
                raise MemoryValidationError("Memory provenance Run does not match source Run")
            await self._run_identity(run_id)
            specs.append(
                {
                    "source_kind": "run",
                    "source_ref": run_id,
                    "run_id": run_id,
                    "source_data": {"run_id": run_id},
                }
            )
            references = (
                ("turn_id", AgentTurnRecord, "turn"),
                ("tool_call_id", ToolCallRecord, "tool_call"),
                ("artifact_id", ArtifactRecord, "artifact"),
            )
            for key, model, source_kind in references:
                reference_id = provenance.get(key)
                if not reference_id:
                    continue
                await self._validate_reference(
                    model,
                    str(reference_id),
                    run_id=run_id,
                    label=key,
                )
                specs.append(
                    {
                        "source_kind": source_kind,
                        "source_ref": str(reference_id),
                        "run_id": run_id,
                        key: str(reference_id),
                        "source_data": {key: str(reference_id)},
                    }
                )
        source_url = provenance.get("url")
        if source_url:
            specs.append(
                {
                    "source_kind": "external",
                    "source_ref": str(source_url),
                    "run_id": run_id,
                    "source_data": {"url": str(source_url)},
                }
            )
        return specs

    async def create(
        self,
        *,
        scope: str,
        kind: str,
        content: str,
        provenance: dict[str, Any],
        confidence: float,
        run_id: str | None = None,
        namespace: MemoryNamespace | None = None,
        memory_key: str | None = None,
        status: str | MemoryStatus = MemoryStatus.active,
        structured_data: dict[str, Any] | None = None,
        importance: float = 0.5,
        utility_score: float = 0.0,
        observed_at: datetime | None = None,
        valid_from: datetime | None = None,
        valid_to: datetime | None = None,
        expires_at: datetime | None = None,
        created_by: str | None = None,
        normalize_kind: bool = True,
        commit: bool = True,
    ) -> PersistedMemoryRecord:
        normalized_content = str(content or "").strip()
        initial_status = _validate_new_memory(
            normalized_content, confidence, importance, utility_score, status, scope, provenance
        )

        namespace, task = await self._resolve_write_namespace(run_id, scope, namespace)

        normalized_kind = normalize_memory_kind(kind)
        if normalize_kind and normalized_kind is None:
            raise MemoryValidationError(f"Unsupported cross-Session Memory kind: {kind}")
        stored_kind = normalized_kind.value if normalize_kind and normalized_kind else kind
        source_specs = await self._source_specs(run_id=run_id, provenance=provenance)
        _require_persistent_sources(scope, source_specs)

        now = utc_now()
        record = PersistedMemoryRecord(
            run_id=run_id,
            created_by=created_by or (task.created_by if task else None),
            memory_key=memory_key or uuid_str(),
            namespace_type=namespace.type.value,
            namespace_id=namespace.id,
            scope=scope,
            kind=stored_kind,
            status=initial_status.value,
            version=1,
            state_version=1,
            content=normalized_content,
            structured_data=structured_data or {},
            provenance=provenance,
            confidence=confidence,
            importance=importance,
            utility_score=utility_score,
            observed_at=_as_utc(observed_at) or now,
            valid_from=_as_utc(valid_from) or now,
            valid_to=_as_utc(valid_to),
            created_at=now,
            updated_at=now,
            expires_at=_as_utc(expires_at),
        )
        self.session.add(record)
        await self.session.flush()
        for spec in source_specs:
            source = MemorySourceRecord(
                memory_id=record.id,
                source_kind=spec["source_kind"],
                source_ref=spec["source_ref"],
                source_hash=_canonical_digest(spec),
                run_id=spec.get("run_id"),
                turn_id=spec.get("turn_id"),
                tool_call_id=spec.get("tool_call_id"),
                artifact_id=spec.get("artifact_id"),
                source_data=spec["source_data"],
                accessible=True,
                created_at=now,
            )
            self.session.add(source)
        self.session.add(
            MemoryAuditRecord(
                memory_id=record.id,
                event_type="created",
                actor=created_by,
                payload={
                    "status": initial_status.value,
                    "namespace": namespace.as_dict(),
                    "kind": stored_kind,
                },
                created_at=now,
            )
        )
        await self.session.flush()
        if commit:
            await self.session.commit()
        return record

    async def _resolve_write_namespace(self, run_id, scope, namespace):
        if run_id is None:
            if namespace is None:
                raise MemoryValidationError("Memory without a source Run requires an explicit isolated namespace")
            return namespace, None
        derived_namespace, task = await self.namespace_for_write(run_id=run_id, scope=scope)
        if namespace is not None and namespace != derived_namespace:
            raise MemoryValidationError("Explicit Memory namespace does not match the source Run")
        return derived_namespace, task

    async def require(self, memory_id: str, *, include_sources: bool = False) -> PersistedMemoryRecord:
        query = select(PersistedMemoryRecord).where(PersistedMemoryRecord.id == memory_id)
        if include_sources:
            query = query.options(selectinload(PersistedMemoryRecord.sources))
        memory = (await self.session.execute(query)).scalar_one_or_none()
        if memory is None:
            raise MemoryValidationError(f"Memory not found: {memory_id}")
        return memory

    async def latest_for_key(
        self,
        *,
        namespace: MemoryNamespace,
        memory_key: str,
        include_sources: bool = False,
    ) -> PersistedMemoryRecord | None:
        query = (
            select(PersistedMemoryRecord)
            .where(
                PersistedMemoryRecord.namespace_type == namespace.type.value,
                PersistedMemoryRecord.namespace_id == namespace.id,
                PersistedMemoryRecord.memory_key == memory_key,
            )
            .order_by(PersistedMemoryRecord.version.desc(), PersistedMemoryRecord.created_at.desc())
            .limit(1)
        )
        if include_sources:
            query = query.options(selectinload(PersistedMemoryRecord.sources))
        return (await self.session.execute(query)).scalar_one_or_none()

    async def transition(
        self,
        memory_id: str,
        target: str | MemoryStatus,
        *,
        expected_state_version: int,
        actor: str | None = None,
        reason: str | None = None,
        commit: bool = True,
    ) -> PersistedMemoryRecord:
        memory = await self.require(memory_id, include_sources=True)
        if memory.state_version != expected_state_version:
            raise MemoryConflictError("Memory state version changed")
        _, target_status = validate_memory_transition(memory.status, target)
        if target_status is MemoryStatus.active and not any(
            source.accessible and source.revoked_at is None for source in memory.sources
        ):
            raise MemoryValidationError("Active persistent Memory requires an accessible source")
        now = utc_now()
        values: dict[str, Any] = {
            "status": target_status.value,
            "state_version": expected_state_version + 1,
            "updated_at": now,
        }
        if target_status in {
            MemoryStatus.superseded,
            MemoryStatus.revoked,
            MemoryStatus.expired,
        }:
            values["valid_to"] = now
        if target_status is MemoryStatus.revoked:
            values["revoked_at"] = now
            values["revoke_reason"] = reason
        result = await self.session.execute(
            update(PersistedMemoryRecord)
            .where(
                PersistedMemoryRecord.id == memory_id,
                PersistedMemoryRecord.state_version == expected_state_version,
            )
            .values(**values)
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise MemoryConflictError("Memory state version changed")
        self.session.add(
            MemoryAuditRecord(
                memory_id=memory_id,
                event_type="status_changed",
                actor=actor,
                reason=reason,
                payload={
                    "from": memory.status,
                    "to": target_status.value,
                    "expected_state_version": expected_state_version,
                },
                created_at=now,
            )
        )
        await self.session.flush()
        if commit:
            await self.session.commit()
        return await self.require(memory_id, include_sources=True)

    def _replacement_record(
        self,
        current,
        *,
        run_id,
        status,
        content,
        structured_data,
        provenance,
        confidence,
        importance,
        valid_from,
        now,
    ) -> PersistedMemoryRecord:
        replacement = PersistedMemoryRecord(
            run_id=run_id,
            created_by=current.created_by,
            memory_key=current.memory_key,
            namespace_type=current.namespace_type,
            namespace_id=current.namespace_id,
            scope=current.scope,
            kind=current.kind,
            status=status.value,
            version=current.version + 1,
            state_version=1,
            content=str(content or "").strip(),
            structured_data=structured_data or {},
            provenance=provenance,
            confidence=current.confidence if confidence is None else confidence,
            importance=current.importance if importance is None else importance,
            utility_score=current.utility_score,
            observed_at=now,
            valid_from=_as_utc(valid_from) or now,
            supersedes_id=current.id,
            consolidation_generation=current.consolidation_generation,
            created_at=now,
            updated_at=now,
        )
        if not replacement.content:
            raise MemoryValidationError("Memory content must be non-empty")
        return replacement

    def _copy_version_sources(self, current, replacement, source_specs, now) -> None:
        copied_refs = {
            (source.source_kind, source.source_ref)
            for source in current.sources
            if source.accessible and source.revoked_at is None
        }
        for source in current.sources:
            if (source.source_kind, source.source_ref) not in copied_refs:
                continue
            self.session.add(_copied_memory_source(source, replacement.id, now))
        for spec in source_specs:
            if (spec["source_kind"], spec["source_ref"]) not in copied_refs:
                self.session.add(_memory_source_from_spec(spec, replacement.id, now))

    async def create_version(
        self,
        memory_id: str,
        *,
        expected_state_version: int,
        content: str,
        provenance: dict[str, Any],
        structured_data: dict[str, Any] | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        valid_from: datetime | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> PersistedMemoryRecord:
        current = await self.require(memory_id, include_sources=True)
        if current.status != MemoryStatus.active.value:
            raise MemoryValidationError("Only active Memory can be superseded")
        if current.state_version != expected_state_version:
            raise MemoryConflictError("Memory state version changed")
        validate_memory_transition(current.status, MemoryStatus.superseded)
        source_specs = await self._source_specs(
            run_id=current.run_id,
            provenance=provenance,
        )
        now = utc_now()
        replacement = self._replacement_record(
            current,
            run_id=current.run_id,
            status=MemoryStatus.active,
            content=content,
            structured_data=structured_data,
            provenance=provenance,
            confidence=confidence,
            importance=importance,
            valid_from=valid_from,
            now=now,
        )
        self.session.add(replacement)
        await self.session.flush()
        self._copy_version_sources(current, replacement, source_specs, now)
        result = await self.session.execute(
            update(PersistedMemoryRecord)
            .where(
                PersistedMemoryRecord.id == current.id,
                PersistedMemoryRecord.state_version == expected_state_version,
                PersistedMemoryRecord.status == MemoryStatus.active.value,
            )
            .values(
                status=MemoryStatus.superseded.value,
                state_version=expected_state_version + 1,
                valid_to=now,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise MemoryConflictError("Memory changed while creating a replacement")
        self.session.add(
            MemoryLinkRecord(
                source_memory_id=replacement.id,
                target_memory_id=current.id,
                relation="supersedes",
                link_data={"reason": reason},
                created_at=now,
            )
        )
        for target_id, event_type, payload in (
            (
                current.id,
                "superseded",
                {"replacement_id": replacement.id, "reason": reason},
            ),
            (
                replacement.id,
                "version_created",
                {"supersedes_id": current.id, "version": replacement.version},
            ),
        ):
            self.session.add(
                MemoryAuditRecord(
                    memory_id=target_id,
                    event_type=event_type,
                    actor=actor,
                    reason=reason,
                    payload=payload,
                    created_at=now,
                )
            )
        await self.session.commit()
        return await self.require(replacement.id, include_sources=True)

    async def create_candidate_version(
        self,
        memory_id: str,
        *,
        expected_state_version: int,
        source_run_id: str,
        content: str,
        provenance: dict[str, Any],
        structured_data: dict[str, Any] | None = None,
        confidence: float | None = None,
        importance: float | None = None,
        valid_from: datetime | None = None,
        actor: str | None = None,
        reason: str | None = None,
    ) -> PersistedMemoryRecord:
        current = await self.require(memory_id, include_sources=True)
        if current.status != MemoryStatus.active.value:
            raise MemoryValidationError("Only active Memory can receive a candidate replacement")
        if current.state_version != expected_state_version:
            raise MemoryConflictError("Memory state version changed")
        source_specs = await self._source_specs(
            run_id=source_run_id,
            provenance=provenance,
        )
        now = utc_now()
        replacement = self._replacement_record(
            current,
            run_id=source_run_id,
            status=MemoryStatus.candidate,
            content=content,
            structured_data=structured_data,
            provenance=provenance,
            confidence=confidence,
            importance=importance,
            valid_from=valid_from,
            now=now,
        )
        self.session.add(replacement)
        await self.session.flush()
        self._copy_version_sources(current, replacement, source_specs, now)
        self.session.add(
            MemoryLinkRecord(
                source_memory_id=replacement.id,
                target_memory_id=current.id,
                relation="candidate_supersedes",
                link_data={"reason": reason},
                created_at=now,
            )
        )
        self.session.add(
            MemoryAuditRecord(
                memory_id=replacement.id,
                event_type="candidate_version_created",
                actor=actor,
                reason=reason,
                payload={"supersedes_id": current.id, "version": replacement.version},
                created_at=now,
            )
        )
        await self.session.commit()
        return await self.require(replacement.id, include_sources=True)

    async def activate_candidate(
        self,
        memory_id: str,
        *,
        expected_state_version: int,
        actor: str,
        reason: str,
    ) -> PersistedMemoryRecord:
        candidate = await self.require(memory_id, include_sources=True)
        normalized_reason = str(reason or "").strip()
        normalized_actor = str(actor or "").strip()
        self._validate_candidate_activation(candidate, expected_state_version, normalized_actor, normalized_reason)

        now = utc_now()
        base = await self._supersede_candidate_base(candidate, now)

        result = await self.session.execute(
            update(PersistedMemoryRecord)
            .where(
                PersistedMemoryRecord.id == candidate.id,
                PersistedMemoryRecord.status == MemoryStatus.candidate.value,
                PersistedMemoryRecord.state_version == expected_state_version,
            )
            .values(
                status=MemoryStatus.active.value,
                state_version=expected_state_version + 1,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise MemoryConflictError("Memory state version changed")
        if base is not None:
            self.session.add(
                MemoryAuditRecord(
                    memory_id=base.id,
                    event_type="superseded",
                    actor=normalized_actor,
                    reason=normalized_reason,
                    payload={"replacement_id": candidate.id},
                    created_at=now,
                )
            )
        self.session.add(
            MemoryAuditRecord(
                memory_id=candidate.id,
                event_type="human_activated",
                actor=normalized_actor,
                reason=normalized_reason,
                payload={
                    "from": MemoryStatus.candidate.value,
                    "to": MemoryStatus.active.value,
                    "expected_state_version": expected_state_version,
                    "supersedes_id": candidate.supersedes_id,
                },
                created_at=now,
            )
        )
        await self.session.commit()
        return await self.require(candidate.id, include_sources=True)

    def _validate_candidate_activation(self, candidate, expected_state_version, actor, reason) -> None:
        if candidate.state_version != expected_state_version:
            raise MemoryConflictError("Memory state version changed")
        if candidate.status != MemoryStatus.candidate.value:
            raise MemoryValidationError("Only candidate Memory can be human-activated")
        if not any(source.accessible and source.revoked_at is None for source in candidate.sources):
            raise MemoryValidationError("Active persistent Memory requires an accessible source")
        if not actor or len(reason) < 3:
            raise MemoryValidationError("Human activation requires an actor and audit reason")

    async def _supersede_candidate_base(self, candidate, now):
        if not candidate.supersedes_id:
            return None
        base = await self.require(candidate.supersedes_id)
        same_lineage = (
            base.status == MemoryStatus.active.value
            and base.namespace_type == candidate.namespace_type
            and base.namespace_id == candidate.namespace_id
            and base.memory_key == candidate.memory_key
            and candidate.version == base.version + 1
        )
        if not same_lineage:
            raise MemoryConflictError("Candidate base version changed")
        result = await self.session.execute(
            update(PersistedMemoryRecord)
            .where(
                PersistedMemoryRecord.id == base.id,
                PersistedMemoryRecord.status == MemoryStatus.active.value,
                PersistedMemoryRecord.state_version == base.state_version,
            )
            .values(
                status=MemoryStatus.superseded.value,
                state_version=base.state_version + 1,
                valid_to=now,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise MemoryConflictError("Candidate base version changed")
        return base

    async def add_link(
        self,
        *,
        source_memory_id: str,
        target_memory_id: str,
        relation: str,
        link_data: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> MemoryLinkRecord:
        if source_memory_id == target_memory_id:
            raise MemoryValidationError("Memory cannot link to itself")
        source = await self.require(source_memory_id)
        target = await self.require(target_memory_id)
        if (source.namespace_type, source.namespace_id) != (
            target.namespace_type,
            target.namespace_id,
        ):
            raise MemoryValidationError("Memory links cannot cross namespaces")
        record = MemoryLinkRecord(
            source_memory_id=source_memory_id,
            target_memory_id=target_memory_id,
            relation=relation,
            link_data=link_data or {},
        )
        self.session.add(record)
        await self.session.flush()
        if commit:
            await self.session.commit()
        return record
