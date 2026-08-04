from __future__ import annotations

from collections.abc import Set
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import ResourceError, StateError, ValidationError
from app.db.models.evolution import AgentEvolutionCandidateRecord
from app.db.session import get_session
from app.evolution import (
    EvaluationThresholds,
    EvolutionCandidateStatus,
    EvolutionDomainError,
)
from app.repositories.evolution import (
    EvolutionRepository,
    candidate_from_record,
    evaluation_from_record,
)
from app.repositories.tool_settings import ToolSettingsRepository, default_tool_states
from app.schemas.evolution import (
    EvolutionAuditView,
    EvolutionCandidateCreateRequest,
    EvolutionCandidateDetailView,
    EvolutionCandidateView,
    EvolutionEvaluationAttachRequest,
    EvolutionEvaluationView,
    EvolutionPromotionRequest,
    EvolutionReviewRequest,
    EvolutionRollbackRequest,
    EvolutionSourceView,
)
from app.tools.registry import sandbox_available

router = APIRouter(
    prefix="/api/agent-evolution/candidates",
    tags=["agent-evolution"],
)


async def get_available_evolution_tools(
    settings: Settings = Depends(get_settings),
    session: AsyncSession = Depends(get_session),
) -> frozenset[str]:
    states = await ToolSettingsRepository(session).get_or_create(
        default_tool_states(settings)
    )
    if not settings.sandbox_enabled or not sandbox_available(settings):
        return frozenset()
    return frozenset(name for name, enabled in states.items() if enabled)


def get_required_evolution_thresholds() -> EvaluationThresholds:
    return EvaluationThresholds()


def _raise_evolution_error(exc: EvolutionDomainError) -> None:
    if exc.code == "EVOLUTION_CANDIDATE_NOT_FOUND":
        raise ResourceError(exc.code, str(exc)) from exc
    if exc.code in {
        "EVOLUTION_NAMESPACE_INVALID",
        "EVOLUTION_NAMESPACE_REQUIRED",
        "EVOLUTION_NAMESPACE_NOT_FOUND",
        "EVOLUTION_NAMESPACE_FILTER_INCOMPLETE",
        "EVOLUTION_SOURCE_NOT_FOUND",
        "EVOLUTION_LINEAGE_INVALID",
        "EVOLUTION_REVIEW_INVALID",
        "EVOLUTION_PROMOTION_TARGET_INVALID",
        "EVOLUTION_ROLLBACK_METADATA_INVALID",
        "EVOLUTION_ROLLBACK_METADATA_TOO_LARGE",
    }:
        raise ValidationError(exc.code, str(exc), exc.details) from exc
    raise StateError(exc.code, str(exc), exc.details) from exc


def _current_evaluation(record: AgentEvolutionCandidateRecord):
    if record.current_evaluation_id is None:
        return None
    return next(
        (
            evaluation
            for evaluation in record.evaluations
            if evaluation.id == record.current_evaluation_id
        ),
        None,
    )


def _candidate_view(record: AgentEvolutionCandidateRecord) -> EvolutionCandidateView:
    candidate = candidate_from_record(record)
    current_evaluation = _current_evaluation(record)
    return EvolutionCandidateView(
        id=record.id,
        namespace_type=record.namespace_type,
        namespace_id=record.namespace_id,
        candidate=candidate,
        candidate_digest=candidate.digest,
        status=EvolutionCandidateStatus(record.status),
        state_version=record.state_version,
        current_evaluation_id=record.current_evaluation_id,
        current_evaluation_verdict=(
            current_evaluation.verdict if current_evaluation is not None else None
        ),
        created_by=record.created_by,
        reviewed_by=record.reviewed_by,
        review_reason=record.review_reason,
        created_at=record.created_at,
        updated_at=record.updated_at,
        executable=False,
        production_promotion_enabled=False,
    )


def _candidate_detail(
    record: AgentEvolutionCandidateRecord,
) -> EvolutionCandidateDetailView:
    summary = _candidate_view(record)
    audits = sorted(record.audit_events, key=lambda item: (item.created_at, item.id))
    rollback = next(
        (
            item.payload
            for item in reversed(audits)
            if item.event_type == "candidate_rolled_back"
        ),
        None,
    )
    return EvolutionCandidateDetailView(
        **summary.model_dump(),
        sources=[
            EvolutionSourceView(
                source_type=item.source_kind,
                source_id=item.source_ref,
                digest=f"sha256:{item.source_hash}",
                accessible=item.accessible and item.revoked_at is None,
                created_at=item.created_at,
                revoked_at=item.revoked_at,
            )
            for item in sorted(
                record.sources,
                key=lambda item: (item.source_kind, item.source_ref),
            )
        ],
        evaluations=[
            EvolutionEvaluationView(
                id=item.id,
                version=item.version,
                manifest=(manifest := evaluation_from_record(item)),
                manifest_digest=manifest.digest,
                verdict=item.verdict,
                evaluator=manifest.evaluator_id,
                issuer=item.issuer,
                created_at=item.created_at,
            )
            for item in sorted(record.evaluations, key=lambda item: item.version)
        ],
        audit_events=[
            EvolutionAuditView(
                id=item.id,
                event_type=item.event_type,
                actor=item.actor,
                reason=item.reason,
                expected_state_version=item.expected_state_version,
                actual_state_version=item.actual_state_version,
                payload=item.payload,
                created_at=item.created_at,
            )
            for item in audits
        ],
        rollback_metadata=rollback,
    )


@router.post("", response_model=EvolutionCandidateDetailView, status_code=201)
async def create_candidate(
    payload: EvolutionCandidateCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> EvolutionCandidateDetailView:
    try:
        record = await EvolutionRepository(session).create(
            namespace_type=payload.namespace_type,
            namespace_id=payload.namespace_id,
            candidate=payload.candidate,
            actor=payload.actor,
        )
        return _candidate_detail(record)
    except EvolutionDomainError as exc:
        await session.rollback()
        _raise_evolution_error(exc)


@router.get("", response_model=list[EvolutionCandidateView])
async def list_candidates(
    namespace_type: str | None = Query(default=None),
    namespace_id: str | None = Query(default=None),
    status: EvolutionCandidateStatus | None = Query(default=None),
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    session: AsyncSession = Depends(get_session),
) -> list[EvolutionCandidateView]:
    try:
        records = await EvolutionRepository(session).list(
            namespace_type=namespace_type,
            namespace_id=namespace_id,
            status=status,
            limit=limit,
        )
        return [_candidate_view(record) for record in records]
    except EvolutionDomainError as exc:
        await session.rollback()
        _raise_evolution_error(exc)


@router.get("/{candidate_id}", response_model=EvolutionCandidateDetailView)
async def get_candidate(
    candidate_id: str,
    session: AsyncSession = Depends(get_session),
) -> EvolutionCandidateDetailView:
    try:
        return _candidate_detail(await EvolutionRepository(session).require(candidate_id))
    except EvolutionDomainError as exc:
        await session.rollback()
        _raise_evolution_error(exc)


@router.post(
    "/{candidate_id}/evaluations",
    response_model=EvolutionCandidateDetailView,
)
async def attach_evaluation(
    candidate_id: str,
    payload: EvolutionEvaluationAttachRequest,
    available_tools: Set[str] = Depends(get_available_evolution_tools),
    required_thresholds: EvaluationThresholds = Depends(
        get_required_evolution_thresholds
    ),
    session: AsyncSession = Depends(get_session),
) -> EvolutionCandidateDetailView:
    try:
        record = await EvolutionRepository(session).attach_evaluation(
            candidate_id,
            manifest=payload.manifest,
            expected_state_version=payload.expected_state_version,
            available_tools=available_tools,
            actor=payload.actor,
            reason=payload.reason,
            required_thresholds=required_thresholds,
        )
        return _candidate_detail(record)
    except EvolutionDomainError as exc:
        await session.rollback()
        _raise_evolution_error(exc)


async def _review_candidate(
    candidate_id: str,
    payload: EvolutionReviewRequest,
    *,
    target: EvolutionCandidateStatus,
    available_tools: Set[str],
    required_thresholds: EvaluationThresholds,
    session: AsyncSession,
) -> EvolutionCandidateDetailView:
    try:
        record = await EvolutionRepository(session).review(
            candidate_id,
            target=target,
            expected_state_version=payload.expected_state_version,
            available_tools=available_tools,
            actor=payload.actor,
            reason=payload.reason,
            required_thresholds=required_thresholds,
        )
        return _candidate_detail(record)
    except EvolutionDomainError as exc:
        await session.rollback()
        _raise_evolution_error(exc)


@router.post("/{candidate_id}/approve", response_model=EvolutionCandidateDetailView)
async def approve_candidate(
    candidate_id: str,
    payload: EvolutionReviewRequest,
    available_tools: Set[str] = Depends(get_available_evolution_tools),
    required_thresholds: EvaluationThresholds = Depends(
        get_required_evolution_thresholds
    ),
    session: AsyncSession = Depends(get_session),
) -> EvolutionCandidateDetailView:
    return await _review_candidate(
        candidate_id,
        payload,
        target=EvolutionCandidateStatus.approved,
        available_tools=available_tools,
        required_thresholds=required_thresholds,
        session=session,
    )


@router.post("/{candidate_id}/reject", response_model=EvolutionCandidateDetailView)
async def reject_candidate(
    candidate_id: str,
    payload: EvolutionReviewRequest,
    available_tools: Set[str] = Depends(get_available_evolution_tools),
    required_thresholds: EvaluationThresholds = Depends(
        get_required_evolution_thresholds
    ),
    session: AsyncSession = Depends(get_session),
) -> EvolutionCandidateDetailView:
    return await _review_candidate(
        candidate_id,
        payload,
        target=EvolutionCandidateStatus.rejected,
        available_tools=available_tools,
        required_thresholds=required_thresholds,
        session=session,
    )


@router.post("/{candidate_id}/promotion", response_model=EvolutionCandidateDetailView)
async def deny_candidate_promotion(
    candidate_id: str,
    payload: EvolutionPromotionRequest,
    available_tools: Set[str] = Depends(get_available_evolution_tools),
    session: AsyncSession = Depends(get_session),
) -> EvolutionCandidateDetailView:
    try:
        await EvolutionRepository(session).deny_promotion(
            candidate_id,
            target=EvolutionCandidateStatus(payload.target),
            expected_state_version=payload.expected_state_version,
            available_tools=available_tools,
            actor=payload.actor,
            reason=payload.reason,
        )
    except EvolutionDomainError as exc:
        await session.rollback()
        _raise_evolution_error(exc)
    raise StateError(
        "EVOLUTION_PROMOTION_DISABLED",
        "Automatic production promotion is disabled.",
    )


@router.post("/{candidate_id}/rollback", response_model=EvolutionCandidateDetailView)
async def rollback_candidate(
    candidate_id: str,
    payload: EvolutionRollbackRequest,
    available_tools: Set[str] = Depends(get_available_evolution_tools),
    session: AsyncSession = Depends(get_session),
) -> EvolutionCandidateDetailView:
    try:
        record = await EvolutionRepository(session).rollback(
            candidate_id,
            expected_state_version=payload.expected_state_version,
            available_tools=available_tools,
            actor=payload.actor,
            reason=payload.reason,
            audience=payload.audience,
            observed_metrics=payload.observed_metrics,
            rollback_criteria=payload.rollback_criteria,
        )
        return _candidate_detail(record)
    except EvolutionDomainError as exc:
        await session.rollback()
        _raise_evolution_error(exc)
