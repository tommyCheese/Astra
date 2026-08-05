from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.memory.consolidation.models import (
    ConsolidationConflictError,
    ConsolidationValidationError,
)
from app.application.memory.consolidation.service import AutoDreamProcessor
from app.common.core.config import Settings, get_settings
from app.common.schemas.memory_consolidation import (
    ConsolidationJobAction,
    ConsolidationJobList,
    ConsolidationJobTrigger,
    ConsolidationJobView,
)
from app.infrastructure.db.models.memory import MemoryConsolidationJobRecord
from app.infrastructure.db.session import get_session
from app.infrastructure.repositories.memory_consolidation import (
    MemoryConsolidationRepository,
    generated_manual_idempotency_key,
)
from app.infrastructure.repositories.memory_consolidation_publication import (
    MemoryConsolidationPublicationService,
)

router = APIRouter(
    prefix="/api/memory/consolidation/jobs",
    tags=["memory-consolidation"],
)


def _safe_profile_snapshot(value: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = dict(value or {})
    profile = snapshot.get("profile")
    safe_profile: dict[str, Any] = {}
    if isinstance(profile, dict):
        documents = profile.get("documents")
        safe_documents: dict[str, Any] = {}
        if isinstance(documents, dict):
            for name, document in documents.items():
                if not isinstance(document, dict):
                    continue
                safe_documents[str(name)] = {
                    key: document.get(key)
                    for key in ("filename", "sha256", "size_bytes", "status")
                    if key in document
                }
        safe_profile = {
            key: profile.get(key)
            for key in (
                "version",
                "composition_schema_version",
                "role_documents",
                "source",
            )
            if key in profile
        }
        safe_profile["documents"] = safe_documents
    return {
        key: snapshot.get(key)
        for key in ("operation", "snapshot_hash", "selected_documents")
        if key in snapshot
    } | ({"profile": safe_profile} if safe_profile else {})


def _view(job: MemoryConsolidationJobRecord) -> ConsolidationJobView:
    return ConsolidationJobView(
        id=job.id,
        namespace_type=job.namespace_type,
        namespace_id=job.namespace_id,
        status=job.status,
        state_version=job.state_version,
        generation=job.generation,
        idempotency_key=job.idempotency_key,
        input_hash=job.input_hash,
        input_manifest=dict(job.input_manifest or {}),
        proposal=dict(job.proposal or {}),
        validation=dict(job.validation or {}),
        profile_snapshot=_safe_profile_snapshot(job.profile_snapshot),
        model_usage=dict(job.model_usage or {}),
        publish_result=dict(job.publish_result or {}),
        error=dict(job.error) if job.error else None,
        lease_owner=job.lease_owner,
        lease_expires_at=job.lease_expires_at,
        rollback_of_id=job.rollback_of_id,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        published_at=job.published_at,
    )


def _raise_api_error(exc: Exception) -> None:
    if isinstance(exc, ConsolidationConflictError):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("", response_model=ConsolidationJobView)
async def trigger_consolidation_job(
    payload: ConsolidationJobTrigger,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ConsolidationJobView:
    """Explicit local trigger; independent from disabled background scheduling."""
    repository = MemoryConsolidationRepository(session)
    try:
        idempotency_key = payload.idempotency_key or (
            generated_manual_idempotency_key(
                payload.namespace_type.value,
                payload.namespace_id,
            )
        )
        job = await repository.create_job(
            namespace_type=payload.namespace_type.value,
            namespace_id=payload.namespace_id,
            idempotency_key=idempotency_key,
        )
        if job.status == "queued":
            job = await AutoDreamProcessor(settings).prepare_job(
                session,
                job.id,
                owner="autodream-api",
            )
        return _view(job)
    except (ConsolidationValidationError, ConsolidationConflictError) as exc:
        _raise_api_error(exc)


@router.get("", response_model=ConsolidationJobList)
async def list_consolidation_jobs(
    namespace_type: str | None = None,
    namespace_id: str | None = None,
    status: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
) -> ConsolidationJobList:
    try:
        jobs = await MemoryConsolidationRepository(session).list_jobs(
            namespace_type=namespace_type,
            namespace_id=namespace_id,
            status=status,
            limit=limit,
        )
        return ConsolidationJobList(jobs=[_view(job) for job in jobs])
    except ConsolidationValidationError as exc:
        _raise_api_error(exc)


@router.get("/{job_id}", response_model=ConsolidationJobView)
async def get_consolidation_job(
    job_id: str,
    session: AsyncSession = Depends(get_session),
) -> ConsolidationJobView:
    try:
        job = await MemoryConsolidationRepository(session).require(job_id)
        return _view(job)
    except ConsolidationValidationError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{job_id}/publish", response_model=ConsolidationJobView)
async def publish_consolidation_job(
    job_id: str,
    payload: ConsolidationJobAction,
    session: AsyncSession = Depends(get_session),
) -> ConsolidationJobView:
    try:
        repository = MemoryConsolidationRepository(session)
        job = await MemoryConsolidationPublicationService(repository).publish(
            job_id,
            expected_state_version=payload.expected_state_version,
            actor=payload.actor,
            reason=payload.reason,
        )
        return _view(job)
    except (ConsolidationValidationError, ConsolidationConflictError) as exc:
        _raise_api_error(exc)


@router.post("/{job_id}/rollback", response_model=ConsolidationJobView)
async def rollback_consolidation_job(
    job_id: str,
    payload: ConsolidationJobAction,
    session: AsyncSession = Depends(get_session),
) -> ConsolidationJobView:
    try:
        repository = MemoryConsolidationRepository(session)
        job = await MemoryConsolidationPublicationService(repository).rollback_published(
            job_id,
            expected_state_version=payload.expected_state_version,
            actor=payload.actor,
            reason=payload.reason,
        )
        return _view(job)
    except (ConsolidationValidationError, ConsolidationConflictError) as exc:
        _raise_api_error(exc)
