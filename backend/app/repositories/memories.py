from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import (
    AgentTurnRecord,
    ArtifactRecord,
    MemoryAuditRecord,
    MemoryLinkRecord,
    MemoryRecallEventRecord,
    MemoryRecord,
    MemorySourceRecord,
    RunRecord,
    TaskRecord,
    ToolCallRecord,
    utc_now,
    uuid_str,
)
from app.memory.domain import (
    MemoryConflictError,
    MemoryNamespace,
    MemoryNamespaceType,
    MemoryStatus,
    MemoryValidationError,
    normalize_memory_kind,
    validate_memory_transition,
)


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


class MemoryRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _run_identity(self, run_id: str) -> tuple[RunRecord, TaskRecord]:
        row = (
            await self.session.execute(
                select(RunRecord, TaskRecord)
                .join(TaskRecord, TaskRecord.id == RunRecord.task_id)
                .where(RunRecord.id == run_id)
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
                raise MemoryValidationError(
                    "Session Memory requires a non-empty Run session identity"
                )
            return (
                MemoryNamespace(MemoryNamespaceType.session, run.memory_session_id),
                task,
            )
        if scope == "user":
            if not task.created_by or not task.created_by.strip():
                raise MemoryValidationError(
                    "User Memory requires a non-empty Task creator identity"
                )
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
        owner_run_id = await self.session.scalar(
            select(model.run_id).where(model.id == reference_id)
        )
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
    ) -> MemoryRecord:
        normalized_content = str(content or "").strip()
        if not normalized_content:
            raise MemoryValidationError("Memory content must be non-empty")
        if len(normalized_content) > 50_000:
            raise MemoryValidationError("Memory content exceeds the 50000 character limit")
        if not 0.0 <= confidence <= 1.0:
            raise MemoryValidationError("Memory confidence must be between 0 and 1")
        if not 0.0 <= importance <= 1.0:
            raise MemoryValidationError("Memory importance must be between 0 and 1")
        if not -1.0 <= utility_score <= 1.0:
            raise MemoryValidationError("Memory utility must be between -1 and 1")
        try:
            initial_status = MemoryStatus(status)
        except ValueError as exc:
            raise MemoryValidationError(f"Unsupported Memory status: {status}") from exc
        if initial_status not in {MemoryStatus.candidate, MemoryStatus.active}:
            raise MemoryValidationError("New Memory must start in candidate or active state")
        if scope in {"task", "session", "user"} and not provenance:
            raise MemoryValidationError("Persistent Memory requires provenance")

        task: TaskRecord | None = None
        if run_id is not None:
            derived_namespace, task = await self.namespace_for_write(
                run_id=run_id,
                scope=scope,
            )
            if namespace is not None and namespace != derived_namespace:
                raise MemoryValidationError(
                    "Explicit Memory namespace does not match the source Run"
                )
            namespace = derived_namespace
        elif namespace is None:
            raise MemoryValidationError(
                "Memory without a source Run requires an explicit isolated namespace"
            )
        if namespace is None:
            raise MemoryValidationError("Memory namespace is required")

        normalized_kind = normalize_memory_kind(kind)
        if normalize_kind and normalized_kind is None:
            raise MemoryValidationError(f"Unsupported cross-Session Memory kind: {kind}")
        stored_kind = normalized_kind.value if normalize_kind and normalized_kind else kind
        source_specs = await self._source_specs(run_id=run_id, provenance=provenance)
        if scope in {"task", "session", "user"} and not source_specs:
            raise MemoryValidationError("Persistent Memory requires a valid source reference")

        now = utc_now()
        record = MemoryRecord(
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

    async def require(self, memory_id: str, *, include_sources: bool = False) -> MemoryRecord:
        query = select(MemoryRecord).where(MemoryRecord.id == memory_id)
        if include_sources:
            query = query.options(selectinload(MemoryRecord.sources))
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
    ) -> MemoryRecord | None:
        query = (
            select(MemoryRecord)
            .where(
                MemoryRecord.namespace_type == namespace.type.value,
                MemoryRecord.namespace_id == namespace.id,
                MemoryRecord.memory_key == memory_key,
            )
            .order_by(MemoryRecord.version.desc(), MemoryRecord.created_at.desc())
            .limit(1)
        )
        if include_sources:
            query = query.options(selectinload(MemoryRecord.sources))
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
    ) -> MemoryRecord:
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
            update(MemoryRecord)
            .where(
                MemoryRecord.id == memory_id,
                MemoryRecord.state_version == expected_state_version,
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
    ) -> MemoryRecord:
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
        replacement = MemoryRecord(
            run_id=current.run_id,
            created_by=current.created_by,
            memory_key=current.memory_key,
            namespace_type=current.namespace_type,
            namespace_id=current.namespace_id,
            scope=current.scope,
            kind=current.kind,
            status=MemoryStatus.active.value,
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
        self.session.add(replacement)
        await self.session.flush()
        copied_refs: set[tuple[str, str]] = set()
        for source in current.sources:
            if not source.accessible or source.revoked_at is not None:
                continue
            copied_refs.add((source.source_kind, source.source_ref))
            self.session.add(
                MemorySourceRecord(
                    memory_id=replacement.id,
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
            )
        for spec in source_specs:
            ref = (spec["source_kind"], spec["source_ref"])
            if ref in copied_refs:
                continue
            self.session.add(
                MemorySourceRecord(
                    memory_id=replacement.id,
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
            )
        result = await self.session.execute(
            update(MemoryRecord)
            .where(
                MemoryRecord.id == current.id,
                MemoryRecord.state_version == expected_state_version,
                MemoryRecord.status == MemoryStatus.active.value,
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
    ) -> MemoryRecord:
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
        replacement = MemoryRecord(
            run_id=source_run_id,
            created_by=current.created_by,
            memory_key=current.memory_key,
            namespace_type=current.namespace_type,
            namespace_id=current.namespace_id,
            scope=current.scope,
            kind=current.kind,
            status=MemoryStatus.candidate.value,
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
        self.session.add(replacement)
        await self.session.flush()
        copied_refs: set[tuple[str, str]] = set()
        for source in current.sources:
            if not source.accessible or source.revoked_at is not None:
                continue
            copied_refs.add((source.source_kind, source.source_ref))
            self.session.add(
                MemorySourceRecord(
                    memory_id=replacement.id,
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
            )
        for spec in source_specs:
            ref = (spec["source_kind"], spec["source_ref"])
            if ref in copied_refs:
                continue
            self.session.add(
                MemorySourceRecord(
                    memory_id=replacement.id,
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
            )
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
    ) -> MemoryRecord:
        candidate = await self.require(memory_id, include_sources=True)
        if candidate.state_version != expected_state_version:
            raise MemoryConflictError("Memory state version changed")
        if candidate.status != MemoryStatus.candidate.value:
            raise MemoryValidationError("Only candidate Memory can be human-activated")
        if not any(
            source.accessible and source.revoked_at is None for source in candidate.sources
        ):
            raise MemoryValidationError("Active persistent Memory requires an accessible source")
        normalized_reason = str(reason or "").strip()
        normalized_actor = str(actor or "").strip()
        if not normalized_actor or len(normalized_reason) < 3:
            raise MemoryValidationError("Human activation requires an actor and audit reason")

        now = utc_now()
        base = None
        if candidate.supersedes_id:
            base = await self.require(candidate.supersedes_id)
            if (
                base.status != MemoryStatus.active.value
                or base.namespace_type != candidate.namespace_type
                or base.namespace_id != candidate.namespace_id
                or base.memory_key != candidate.memory_key
                or candidate.version != base.version + 1
            ):
                raise MemoryConflictError("Candidate base version changed")
            result = await self.session.execute(
                update(MemoryRecord)
                .where(
                    MemoryRecord.id == base.id,
                    MemoryRecord.status == MemoryStatus.active.value,
                    MemoryRecord.state_version == base.state_version,
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

        result = await self.session.execute(
            update(MemoryRecord)
            .where(
                MemoryRecord.id == candidate.id,
                MemoryRecord.status == MemoryStatus.candidate.value,
                MemoryRecord.state_version == expected_state_version,
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
    ) -> list[MemoryRecord]:
        query = select(MemoryRecord).where(MemoryRecord.confidence >= min_confidence)
        if include_sources:
            query = query.options(selectinload(MemoryRecord.sources))
        if scope:
            query = query.where(MemoryRecord.scope == scope)
        if kind:
            query = query.where(MemoryRecord.kind == kind)
        if run_id:
            query = query.where(MemoryRecord.run_id == run_id)
        namespace_list = list(namespaces or [])
        if namespace_list:
            query = query.where(
                or_(
                    *[
                        and_(
                            MemoryRecord.namespace_type == namespace.type.value,
                            MemoryRecord.namespace_id == namespace.id,
                        )
                        for namespace in namespace_list
                    ]
                )
            )
        status_list = [
            MemoryStatus(status).value if not isinstance(status, MemoryStatus) else status.value
            for status in statuses or []
        ]
        if status_list:
            query = query.where(MemoryRecord.status.in_(status_list))
        if not include_expired:
            now = utc_now()
            query = query.where(
                or_(MemoryRecord.expires_at.is_(None), MemoryRecord.expires_at > now),
                or_(MemoryRecord.valid_to.is_(None), MemoryRecord.valid_to > now),
            )
        query = query.order_by(MemoryRecord.updated_at.desc(), MemoryRecord.id).limit(limit)
        return list((await self.session.execute(query)).scalars().all())

    async def history(
        self,
        *,
        namespace: MemoryNamespace,
        memory_key: str,
        as_of: datetime | None = None,
    ) -> list[MemoryRecord]:
        query = select(MemoryRecord).where(
            MemoryRecord.namespace_type == namespace.type.value,
            MemoryRecord.namespace_id == namespace.id,
            MemoryRecord.memory_key == memory_key,
        )
        if as_of is not None:
            instant = _as_utc(as_of)
            query = query.where(
                MemoryRecord.valid_from <= instant,
                or_(MemoryRecord.valid_to.is_(None), MemoryRecord.valid_to > instant),
            )
        query = query.order_by(MemoryRecord.version)
        return list((await self.session.execute(query)).scalars().all())

    async def record_recall_event(
        self,
        *,
        run_id: str,
        query_hash: str,
        policy_version: str,
        namespace_manifest: list[dict[str, str]],
        candidates: list[dict[str, Any]],
        selected: list[dict[str, Any]],
        excluded: list[dict[str, Any]],
        turn_id: str | None = None,
        commit: bool = True,
    ) -> MemoryRecallEventRecord:
        await self._run_identity(run_id)
        if len(query_hash) != 64:
            raise MemoryValidationError("Memory recall query hash must be a SHA-256 digest")
        event = MemoryRecallEventRecord(
            run_id=run_id,
            turn_id=turn_id,
            query_hash=query_hash,
            policy_version=str(policy_version or "").strip(),
            namespace_manifest=namespace_manifest,
            candidates=candidates,
            selected=selected,
            excluded=excluded,
            feedback={},
            created_at=utc_now(),
            updated_at=utc_now(),
        )
        if not event.policy_version:
            raise MemoryValidationError("Memory recall policy version is required")
        self.session.add(event)
        await self.session.flush()
        if commit:
            await self.session.commit()
        return event

    async def record_recall_feedback(
        self,
        recall_event_id: str,
        *,
        outcome: str,
        utility_delta: float,
        details: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> MemoryRecallEventRecord:
        if outcome not in {"helpful", "neutral", "harmful"}:
            raise MemoryValidationError("Unsupported Memory recall outcome")
        if not -1.0 <= utility_delta <= 1.0:
            raise MemoryValidationError("Memory recall utility delta must be between -1 and 1")
        event = await self.session.get(MemoryRecallEventRecord, recall_event_id)
        if event is None:
            raise MemoryValidationError(f"Memory recall event not found: {recall_event_id}")
        event.feedback = {
            "outcome": outcome,
            "utility_delta": utility_delta,
            "details": details or {},
        }
        event.updated_at = utc_now()
        await self.session.flush()
        if commit:
            await self.session.commit()
        return event

    async def materialize_expired(
        self,
        *,
        as_of: datetime | None = None,
        limit: int = 500,
        commit: bool = True,
    ) -> int:
        instant = _as_utc(as_of) or utc_now()
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
                continue
            materialized += 1
            self.session.add(
                MemoryAuditRecord(
                    memory_id=memory.id,
                    event_type="expiration_materialized",
                    actor="memory-retention",
                    reason="expires_at_elapsed",
                    payload={
                        "expires_at": memory.expires_at.isoformat() if memory.expires_at else None
                    },
                    created_at=instant,
                )
            )
        await self.session.flush()
        if commit:
            await self.session.commit()
        return materialized
