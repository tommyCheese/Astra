from __future__ import annotations

import hashlib
import json
from collections.abc import Set
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.evolution import (
    EvaluationManifest,
    EvaluationThresholds,
    EvolutionCandidate,
    EvolutionCandidateState,
    EvolutionCandidateStatus,
    EvolutionDomainError,
    EvolutionSourceType,
    assert_candidate_authority,
    evaluate_manifest,
    transition_candidate_state,
)
from app.infrastructure.db.model_base import utc_now
from app.infrastructure.db.models.conversations import TaskRecord
from app.infrastructure.db.models.evolution import (
    AgentEvolutionAuditRecord,
    AgentEvolutionCandidateRecord,
    AgentEvolutionEvaluationRecord,
    AgentEvolutionSourceRecord,
)
from app.infrastructure.db.models.memory import PersistedMemoryRecord
from app.infrastructure.db.models.runs import AgentTurnRecord, RunRecord
from app.infrastructure.db.models.workspaces import ArtifactRecord

_NAMESPACE_TYPES = frozenset({"run", "task", "workspace", "user"})
_ROLLOUT_STATES = frozenset(
    {
        EvolutionCandidateStatus.shadow,
        EvolutionCandidateStatus.canary,
        EvolutionCandidateStatus.promoted,
    }
)
_MAX_ROLLBACK_METADATA_BYTES = 20_000


def _raw_digest(value: str) -> str:
    return value.removeprefix("sha256:")


def _qualified_digest(value: str) -> str:
    return value if value.startswith("sha256:") else f"sha256:{value}"


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _candidate_from_record(record: AgentEvolutionCandidateRecord) -> EvolutionCandidate:
    candidate = EvolutionCandidate.model_validate(record.content)
    persisted_digest = _qualified_digest(record.content_digest)
    if candidate.digest != persisted_digest:
        raise EvolutionDomainError(
            "EVOLUTION_CANDIDATE_CORRUPTED",
            "Persisted evolution candidate does not match its immutable digest.",
            {"candidate_id": record.id},
        )
    return candidate


def _manifest_from_record(
    record: AgentEvolutionEvaluationRecord,
) -> EvaluationManifest:
    manifest = EvaluationManifest.model_validate(record.manifest)
    persisted_digest = _qualified_digest(record.manifest_digest)
    if manifest.digest != persisted_digest:
        raise EvolutionDomainError(
            "EVOLUTION_EVALUATION_CORRUPTED",
            "Persisted evaluation does not match its immutable digest.",
            {"evaluation_id": record.id},
        )
    return manifest


def _state_from_record(
    record: AgentEvolutionCandidateRecord,
    evaluation: AgentEvolutionEvaluationRecord | None,
) -> EvolutionCandidateState:
    return EvolutionCandidateState(
        candidate_digest=_qualified_digest(record.content_digest),
        status=record.status,
        state_version=record.state_version,
        evaluation_digest=(
            _qualified_digest(evaluation.manifest_digest) if evaluation is not None else None
        ),
    )


def _candidate_created_audit(record, candidate, actor, now):
    return AgentEvolutionAuditRecord(
        candidate_id=record.id,
        event_type="candidate_created",
        actor=actor,
        actual_state_version=1,
        payload={
            "candidate_digest": candidate.digest,
            "candidate_type": candidate.candidate_type.value,
            "target": candidate.target.value,
            "source_count": len(candidate.source_refs),
            "executable": False,
        },
        created_at=now,
    )


class EvolutionRepository:
    """Persistence boundary for immutable, non-executable evolution candidates."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def _validate_namespace(self, namespace_type: str, namespace_id: str) -> None:
        normalized_id = namespace_id.strip()
        if namespace_type not in _NAMESPACE_TYPES:
            raise EvolutionDomainError(
                "EVOLUTION_NAMESPACE_INVALID",
                "Evolution candidate namespace type is unsupported.",
                {"namespace_type": namespace_type},
            )
        if not normalized_id:
            raise EvolutionDomainError(
                "EVOLUTION_NAMESPACE_REQUIRED",
                "Evolution candidate requires an explicit namespace identity.",
            )
        exists = True
        if namespace_type == "run":
            exists = await self.session.get(RunRecord, normalized_id) is not None
        elif namespace_type == "task":
            exists = await self.session.get(TaskRecord, normalized_id) is not None
        elif namespace_type == "workspace":
            exists = (
                await self.session.scalar(
                    select(TaskRecord.id).where(TaskRecord.workspace_id == normalized_id).limit(1)
                )
                is not None
            )
        elif namespace_type == "user":
            exists = (
                await self.session.scalar(
                    select(TaskRecord.id).where(TaskRecord.created_by == normalized_id).limit(1)
                )
                is not None
            )
        if not exists:
            raise EvolutionDomainError(
                "EVOLUTION_NAMESPACE_NOT_FOUND",
                "Evolution candidate namespace identity does not exist.",
                {"namespace_type": namespace_type, "namespace_id": normalized_id},
            )

    async def _source_record_values(
        self,
        *,
        source_type: EvolutionSourceType,
        source_id: str,
        digest: str,
    ) -> dict[str, Any]:
        run_id: str | None = None
        memory_id: str | None = None
        record: Any | None = None
        if source_type == EvolutionSourceType.run:
            record = await self.session.get(RunRecord, source_id)
            run_id = source_id
        elif source_type == EvolutionSourceType.turn:
            record = await self.session.get(AgentTurnRecord, source_id)
            run_id = record.run_id if record is not None else None
        elif source_type == EvolutionSourceType.memory:
            record = await self.session.get(PersistedMemoryRecord, source_id)
            memory_id = source_id
            run_id = record.run_id if record is not None else None
        elif source_type == EvolutionSourceType.artifact:
            record = await self.session.get(ArtifactRecord, source_id)
            run_id = record.run_id if record is not None else None
        elif source_type == EvolutionSourceType.evaluation:
            record = await self.session.get(AgentEvolutionEvaluationRecord, source_id)
        elif source_type == EvolutionSourceType.case:
            # Evaluation cases are externally frozen references in v1. Their
            # supplied digest is the immutable identity until a case catalog
            # is introduced.
            record = source_id
        if record is None:
            raise EvolutionDomainError(
                "EVOLUTION_SOURCE_NOT_FOUND",
                "Evolution candidate source does not exist.",
                {"source_type": source_type.value, "source_id": source_id},
            )
        return {
            "source_kind": source_type.value,
            "source_ref": source_id,
            "source_hash": _raw_digest(digest),
            "run_id": run_id,
            "memory_id": memory_id,
            "source_data": {
                "source_type": source_type.value,
                "source_id": source_id,
                "digest": digest,
            },
        }

    async def create(
        self,
        *,
        namespace_type: str,
        namespace_id: str,
        candidate: EvolutionCandidate,
        actor: str,
        commit: bool = True,
    ) -> AgentEvolutionCandidateRecord:
        normalized_namespace_id = namespace_id.strip()
        await self._validate_namespace(namespace_type, normalized_namespace_id)

        await self._ensure_revision_available(namespace_type, normalized_namespace_id, candidate)
        await self._validate_supersession(namespace_type, normalized_namespace_id, candidate)

        source_values = [
            await self._source_record_values(
                source_type=source.source_type,
                source_id=source.source_id,
                digest=source.digest,
            )
            for source in candidate.source_refs
        ]
        now = utc_now()
        candidate_payload = candidate.model_dump(mode="json")
        source_manifest = {
            "schema_version": 1,
            "sources": [source.model_dump(mode="json") for source in candidate.source_refs],
        }
        record = AgentEvolutionCandidateRecord(
            candidate_key=candidate.candidate_key,
            revision=candidate.revision,
            supersedes_id=candidate.supersedes_id,
            candidate_type=candidate.candidate_type.value,
            target_component=candidate.target.value,
            namespace_type=namespace_type,
            namespace_id=normalized_namespace_id,
            status=EvolutionCandidateStatus.draft.value,
            state_version=1,
            content=candidate_payload,
            content_digest=_raw_digest(candidate.digest),
            source_manifest=source_manifest,
            source_manifest_digest=_stable_digest(source_manifest),
            environment_constraints={
                "items": [
                    item.model_dump(mode="json") for item in candidate.environment_constraints
                ]
            },
            created_by=actor,
            created_at=now,
            updated_at=now,
        )
        self.session.add(record)
        await self.session.flush()
        for values in source_values:
            self.session.add(
                AgentEvolutionSourceRecord(
                    candidate_id=record.id, **values, accessible=True, created_at=now
                )
            )
        self.session.add(_candidate_created_audit(record, candidate, actor, now))
        await self.session.flush()
        if commit:
            await self.session.commit()
        return await self.require(record.id)

    async def _ensure_revision_available(self, namespace_type, namespace_id, candidate):
        existing = await self.session.scalar(
            select(AgentEvolutionCandidateRecord.id).where(
                AgentEvolutionCandidateRecord.namespace_type == namespace_type,
                AgentEvolutionCandidateRecord.namespace_id == namespace_id,
                AgentEvolutionCandidateRecord.candidate_key == candidate.candidate_key,
                AgentEvolutionCandidateRecord.revision == candidate.revision,
            )
        )
        if existing is not None:
            raise EvolutionDomainError(
                "EVOLUTION_CANDIDATE_EXISTS",
                "This immutable candidate revision already exists in the namespace.",
                {"candidate_id": existing},
            )

    async def _validate_supersession(self, namespace_type, namespace_id, candidate):
        if candidate.supersedes_id is not None:
            previous = await self.session.get(
                AgentEvolutionCandidateRecord,
                candidate.supersedes_id,
            )
            if (
                previous is None
                or previous.namespace_type != namespace_type
                or previous.namespace_id != namespace_id
                or previous.candidate_key != candidate.candidate_key
                or previous.revision != candidate.revision - 1
            ):
                raise EvolutionDomainError(
                    "EVOLUTION_LINEAGE_INVALID",
                    "Candidate supersession must reference the immediately preceding "
                    "revision in the same namespace.",
                )

    async def require(
        self,
        candidate_id: str,
    ) -> AgentEvolutionCandidateRecord:
        record = (
            await self.session.execute(
                select(AgentEvolutionCandidateRecord)
                .where(AgentEvolutionCandidateRecord.id == candidate_id)
                .execution_options(populate_existing=True)
                .options(
                    selectinload(AgentEvolutionCandidateRecord.sources),
                    selectinload(AgentEvolutionCandidateRecord.evaluations),
                    selectinload(AgentEvolutionCandidateRecord.audit_events),
                )
            )
        ).scalar_one_or_none()
        if record is None:
            raise EvolutionDomainError(
                "EVOLUTION_CANDIDATE_NOT_FOUND",
                "Evolution candidate was not found.",
                {"candidate_id": candidate_id},
            )
        _candidate_from_record(record)
        for evaluation in record.evaluations:
            _manifest_from_record(evaluation)
        return record

    async def list(
        self,
        *,
        namespace_type: str | None = None,
        namespace_id: str | None = None,
        status: EvolutionCandidateStatus | None = None,
        limit: int = 100,
    ) -> list[AgentEvolutionCandidateRecord]:
        if (namespace_type is None) != (namespace_id is None):
            raise EvolutionDomainError(
                "EVOLUTION_NAMESPACE_FILTER_INCOMPLETE",
                "Namespace type and namespace ID must be supplied together.",
            )
        if namespace_type is not None and (
            namespace_type not in _NAMESPACE_TYPES or not (namespace_id or "").strip()
        ):
            raise EvolutionDomainError(
                "EVOLUTION_NAMESPACE_INVALID",
                "Evolution candidate namespace filter is invalid.",
                {"namespace_type": namespace_type},
            )
        query = select(AgentEvolutionCandidateRecord).options(
            selectinload(AgentEvolutionCandidateRecord.sources),
            selectinload(AgentEvolutionCandidateRecord.evaluations),
            selectinload(AgentEvolutionCandidateRecord.audit_events),
        )
        if namespace_type is not None and namespace_id is not None:
            query = query.where(
                AgentEvolutionCandidateRecord.namespace_type == namespace_type,
                AgentEvolutionCandidateRecord.namespace_id == namespace_id,
            )
        if status is not None:
            query = query.where(AgentEvolutionCandidateRecord.status == status.value)
        records = list(
            (
                await self.session.scalars(
                    query.order_by(
                        AgentEvolutionCandidateRecord.updated_at.desc(),
                        AgentEvolutionCandidateRecord.id.asc(),
                    ).limit(limit)
                )
            ).all()
        )
        for record in records:
            _candidate_from_record(record)
        return records

    async def _current_evaluation(
        self,
        record: AgentEvolutionCandidateRecord,
    ) -> AgentEvolutionEvaluationRecord | None:
        if record.current_evaluation_id is None:
            return None
        evaluation = await self.session.get(
            AgentEvolutionEvaluationRecord,
            record.current_evaluation_id,
        )
        if evaluation is None or evaluation.candidate_id != record.id:
            raise EvolutionDomainError(
                "EVOLUTION_EVALUATION_CORRUPTED",
                "Candidate references a missing or unrelated evaluation.",
                {"candidate_id": record.id},
            )
        _manifest_from_record(evaluation)
        return evaluation

    async def attach_evaluation(
        self,
        candidate_id: str,
        *,
        manifest: EvaluationManifest,
        expected_state_version: int,
        available_tools: Set[str],
        actor: str,
        reason: str | None = None,
        required_thresholds: EvaluationThresholds | None = None,
        commit: bool = True,
    ) -> AgentEvolutionCandidateRecord:
        record = await self.require(candidate_id)
        candidate = _candidate_from_record(record)
        if record.state_version != expected_state_version:
            raise EvolutionDomainError(
                "EVOLUTION_STATE_STALE",
                "Evolution candidate state has changed.",
                {
                    "expected_state_version": expected_state_version,
                    "actual_state_version": record.state_version,
                },
            )
        if manifest.candidate_digest != candidate.digest:
            raise EvolutionDomainError(
                "EVOLUTION_EVALUATION_MISMATCH",
                "Evaluation manifest belongs to a different candidate revision.",
            )
        next_state = await self._evaluation_next_state(
            record, candidate, manifest, expected_state_version, available_tools
        )

        decision = evaluate_manifest(
            manifest,
            required_thresholds=required_thresholds,
        )
        next_version = (
            await self.session.scalar(
                select(func.max(AgentEvolutionEvaluationRecord.version)).where(
                    AgentEvolutionEvaluationRecord.candidate_id == candidate_id
                )
            )
            or 0
        ) + 1
        now = utc_now()
        evaluation = AgentEvolutionEvaluationRecord(
            candidate_id=candidate_id,
            version=next_version,
            manifest=manifest.model_dump(mode="json"),
            manifest_digest=_raw_digest(manifest.digest),
            evaluator=manifest.evaluator_id[:160],
            issuer=actor[:160],
            verdict="passed" if decision.passed else "failed",
            created_at=now,
        )
        self.session.add(evaluation)
        await self.session.flush()
        result = await self.session.execute(
            update(AgentEvolutionCandidateRecord)
            .where(
                AgentEvolutionCandidateRecord.id == candidate_id,
                AgentEvolutionCandidateRecord.state_version == expected_state_version,
            )
            .values(
                status=next_state.status.value,
                state_version=next_state.state_version,
                current_evaluation_id=evaluation.id,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise EvolutionDomainError(
                "EVOLUTION_STATE_STALE",
                "Evolution candidate state changed while attaching evaluation.",
            )
        self.session.add(
            AgentEvolutionAuditRecord(
                candidate_id=candidate_id,
                event_type="evaluation_attached",
                actor=actor,
                reason=reason,
                expected_state_version=expected_state_version,
                actual_state_version=next_state.state_version,
                payload={
                    "evaluation_id": evaluation.id,
                    "evaluation_version": evaluation.version,
                    "manifest_digest": manifest.digest,
                    "verdict": evaluation.verdict,
                    "issue_codes": [issue.code for issue in decision.issues],
                },
                created_at=now,
            )
        )
        await self.session.flush()
        if commit:
            await self.session.commit()
        return await self.require(candidate_id)

    async def _evaluation_next_state(
        self, record, candidate, manifest, expected_state_version, available_tools
    ) -> EvolutionCandidateState:
        current_status = EvolutionCandidateStatus(record.status)
        if current_status not in {
            EvolutionCandidateStatus.draft,
            EvolutionCandidateStatus.evaluating,
        }:
            raise EvolutionDomainError(
                "EVOLUTION_TRANSITION_INVALID",
                "Evaluations may only be attached to draft or evaluating candidates.",
                {"status": current_status.value},
            )
        if current_status == EvolutionCandidateStatus.draft:
            return transition_candidate_state(
                candidate,
                _state_from_record(record, await self._current_evaluation(record)),
                EvolutionCandidateStatus.evaluating,
                expected_state_version=expected_state_version,
                available_tools=available_tools,
            )
        assert_candidate_authority(candidate, available_tools=available_tools)
        return EvolutionCandidateState(
            candidate_digest=candidate.digest,
            status=EvolutionCandidateStatus.evaluating,
            state_version=expected_state_version + 1,
            evaluation_digest=manifest.digest,
        )

    async def review(
        self,
        candidate_id: str,
        *,
        target: EvolutionCandidateStatus,
        expected_state_version: int,
        available_tools: Set[str],
        actor: str,
        reason: str,
        required_thresholds: EvaluationThresholds | None = None,
        commit: bool = True,
    ) -> AgentEvolutionCandidateRecord:
        if target not in {
            EvolutionCandidateStatus.approved,
            EvolutionCandidateStatus.rejected,
        }:
            raise EvolutionDomainError(
                "EVOLUTION_REVIEW_INVALID",
                "Review can only approve or reject a candidate.",
            )
        record = await self.require(candidate_id)
        candidate = _candidate_from_record(record)
        evaluation = await self._current_evaluation(record)
        manifest = _manifest_from_record(evaluation) if evaluation is not None else None
        previous_status = record.status
        next_state = transition_candidate_state(
            candidate,
            _state_from_record(record, evaluation),
            target,
            expected_state_version=expected_state_version,
            available_tools=available_tools,
            evaluation_manifest=manifest,
            required_thresholds=required_thresholds,
            promotion_enabled=False,
        )
        now = utc_now()
        result = await self.session.execute(
            update(AgentEvolutionCandidateRecord)
            .where(
                AgentEvolutionCandidateRecord.id == candidate_id,
                AgentEvolutionCandidateRecord.state_version == expected_state_version,
            )
            .values(
                status=next_state.status.value,
                state_version=next_state.state_version,
                reviewed_by=actor,
                review_reason=reason,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise EvolutionDomainError(
                "EVOLUTION_STATE_STALE",
                "Evolution candidate state changed during review.",
            )
        self.session.add(
            AgentEvolutionAuditRecord(
                candidate_id=candidate_id,
                event_type=f"candidate_{target.value}",
                actor=actor,
                reason=reason,
                expected_state_version=expected_state_version,
                actual_state_version=next_state.state_version,
                payload={
                    "from": previous_status,
                    "to": target.value,
                    "evaluation_id": evaluation.id if evaluation is not None else None,
                    "evaluation_digest": next_state.evaluation_digest,
                    "executable": False,
                },
                created_at=now,
            )
        )
        await self.session.flush()
        if commit:
            await self.session.commit()
        return await self.require(candidate_id)

    async def deny_promotion(
        self,
        candidate_id: str,
        *,
        target: EvolutionCandidateStatus,
        expected_state_version: int,
        available_tools: Set[str],
        actor: str,
        reason: str,
    ) -> None:
        if target not in _ROLLOUT_STATES:
            raise EvolutionDomainError(
                "EVOLUTION_PROMOTION_TARGET_INVALID",
                "Promotion target must be shadow, canary, or promoted.",
            )
        record = await self.require(candidate_id)
        candidate = _candidate_from_record(record)
        evaluation = await self._current_evaluation(record)
        transition_candidate_state(
            candidate,
            _state_from_record(record, evaluation),
            target,
            expected_state_version=expected_state_version,
            available_tools=available_tools,
            evaluation_manifest=(
                _manifest_from_record(evaluation) if evaluation is not None else None
            ),
            promotion_enabled=False,
        )
        raise EvolutionDomainError(
            "EVOLUTION_PROMOTION_DISABLED",
            "Automatic production promotion is disabled.",
            {"actor": actor, "reason": reason, "requested_status": target.value},
        )

    async def rollback(
        self,
        candidate_id: str,
        *,
        expected_state_version: int,
        available_tools: Set[str],
        actor: str,
        reason: str,
        audience: dict[str, Any],
        observed_metrics: dict[str, Any],
        rollback_criteria: dict[str, Any],
        commit: bool = True,
    ) -> AgentEvolutionCandidateRecord:
        rollback_metadata = {
            "audience": audience,
            "observed_metrics": observed_metrics,
            "rollback_criteria": rollback_criteria,
        }
        try:
            encoded = json.dumps(
                rollback_metadata,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise EvolutionDomainError(
                "EVOLUTION_ROLLBACK_METADATA_INVALID",
                "Rollback metadata must be finite JSON data.",
            ) from exc
        if len(encoded) > _MAX_ROLLBACK_METADATA_BYTES:
            raise EvolutionDomainError(
                "EVOLUTION_ROLLBACK_METADATA_TOO_LARGE",
                "Rollback metadata exceeds the bounded audit size.",
                {"max_bytes": _MAX_ROLLBACK_METADATA_BYTES},
            )

        record = await self.require(candidate_id)
        candidate = _candidate_from_record(record)
        evaluation = await self._current_evaluation(record)
        next_state = transition_candidate_state(
            candidate,
            _state_from_record(record, evaluation),
            EvolutionCandidateStatus.rolled_back,
            expected_state_version=expected_state_version,
            available_tools=available_tools,
            promotion_enabled=False,
        )
        now = utc_now()
        result = await self.session.execute(
            update(AgentEvolutionCandidateRecord)
            .where(
                AgentEvolutionCandidateRecord.id == candidate_id,
                AgentEvolutionCandidateRecord.state_version == expected_state_version,
            )
            .values(
                status=next_state.status.value,
                state_version=next_state.state_version,
                reviewed_by=actor,
                review_reason=reason,
                updated_at=now,
            )
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise EvolutionDomainError(
                "EVOLUTION_STATE_STALE",
                "Evolution candidate state changed during rollback.",
            )
        self.session.add(
            AgentEvolutionAuditRecord(
                candidate_id=candidate_id,
                event_type="candidate_rolled_back",
                actor=actor,
                reason=reason,
                expected_state_version=expected_state_version,
                actual_state_version=next_state.state_version,
                payload={
                    **rollback_metadata,
                    "candidate_digest": candidate.digest,
                    "evaluation_id": evaluation.id if evaluation is not None else None,
                    "executable": False,
                },
                created_at=now,
            )
        )
        await self.session.flush()
        if commit:
            await self.session.commit()
        return await self.require(candidate_id)


def candidate_from_record(record: AgentEvolutionCandidateRecord) -> EvolutionCandidate:
    return _candidate_from_record(record)


def evaluation_from_record(
    record: AgentEvolutionEvaluationRecord,
) -> EvaluationManifest:
    return _manifest_from_record(record)
