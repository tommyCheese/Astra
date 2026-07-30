from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.evolution.domain import (
    EvaluationManifest,
    EvolutionCandidate,
    EvolutionCandidateStatus,
)

EvolutionNamespaceType = Literal["run", "task", "workspace", "user"]


class EvolutionRequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class EvolutionCandidateCreateRequest(EvolutionRequestModel):
    namespace_type: EvolutionNamespaceType
    namespace_id: str = Field(min_length=1, max_length=120)
    actor: str = Field(min_length=1, max_length=120)
    candidate: EvolutionCandidate


class EvolutionEvaluationAttachRequest(EvolutionRequestModel):
    expected_state_version: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=4_000)
    manifest: EvaluationManifest


class EvolutionReviewRequest(EvolutionRequestModel):
    expected_state_version: int = Field(ge=1)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=4_000)


class EvolutionRollbackRequest(EvolutionReviewRequest):
    audience: dict[str, Any] = Field(default_factory=dict)
    observed_metrics: dict[str, Any] = Field(default_factory=dict)
    rollback_criteria: dict[str, Any] = Field(default_factory=dict)


class EvolutionPromotionRequest(EvolutionReviewRequest):
    target: Literal["shadow", "canary", "promoted"]


class EvolutionSourceView(BaseModel):
    source_type: str
    source_id: str
    digest: str
    accessible: bool
    created_at: datetime
    revoked_at: datetime | None = None


class EvolutionEvaluationView(BaseModel):
    id: str
    version: int
    manifest: EvaluationManifest
    manifest_digest: str
    verdict: Literal["passed", "failed"]
    evaluator: str
    issuer: str
    created_at: datetime


class EvolutionAuditView(BaseModel):
    id: int
    event_type: str
    actor: str | None = None
    reason: str | None = None
    expected_state_version: int | None = None
    actual_state_version: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class EvolutionCandidateView(BaseModel):
    id: str
    namespace_type: EvolutionNamespaceType
    namespace_id: str
    candidate: EvolutionCandidate
    candidate_digest: str
    status: EvolutionCandidateStatus
    state_version: int
    current_evaluation_id: str | None = None
    current_evaluation_verdict: Literal["passed", "failed"] | None = None
    created_by: str | None = None
    reviewed_by: str | None = None
    review_reason: str | None = None
    created_at: datetime
    updated_at: datetime
    executable: Literal[False] = False
    production_promotion_enabled: Literal[False] = False


class EvolutionCandidateDetailView(EvolutionCandidateView):
    sources: list[EvolutionSourceView] = Field(default_factory=list)
    evaluations: list[EvolutionEvaluationView] = Field(default_factory=list)
    audit_events: list[EvolutionAuditView] = Field(default_factory=list)
    rollback_metadata: dict[str, Any] | None = None
