from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.grounding.schemas import Citation as GroundingCitation
from app.grounding.schemas import Claim as GroundingClaim
from app.schemas.agent.execution_state import CompletionDecision
from app.schemas.agent.types import AnswerMode, AssuranceLevel


class SourceReference(BaseModel):
    url: str
    title: str | None = None
    retrieved_at: str | None = None


class Finding(BaseModel):
    text: str
    source_urls: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    claims: list[GroundingClaim] = Field(default_factory=list)
    citations: list[GroundingCitation] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    failed_sources: list[dict[str, Any]] = Field(default_factory=list)
    source_quality: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    verification_notes: list[str] = Field(default_factory=list)
    memory_references: list[dict[str, Any]] = Field(default_factory=list)
    audit_refs: dict[str, Any] = Field(default_factory=dict)


class MemoryRecord(BaseModel):
    id: str | None = None
    memory_key: str | None = None
    namespace_type: str | None = None
    namespace_id: str | None = None
    scope: str
    kind: str
    status: str = "candidate"
    version: int = 1
    state_version: int = 1
    content: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    importance: float = 0.5
    utility_score: float = 0.0
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: str = "error"
    evidence_refs: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationOutcome(BaseModel):
    validator: str
    passed: bool
    blocking: bool = True
    requirement_ids: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class VerificationReport(BaseModel):
    status: str
    assurance_level: AssuranceLevel = AssuranceLevel.full
    source_count: int = 0
    caveat_count: int = 0
    low_quality_sources: list[dict[str, Any]] = Field(default_factory=list)
    failed_sources: list[dict[str, Any]] = Field(default_factory=list)
    memory_references: list[dict[str, Any]] = Field(default_factory=list)
    invalid_artifact_references: int = 0
    notes: list[str] = Field(default_factory=list)
    validation_outcomes: list[ValidationOutcome] = Field(default_factory=list)


class FailedSource(BaseModel):
    url: str | None = None
    title: str | None = None
    type: str | None = None
    category: str | None = None
    code: str | None = None
    message: str | None = None
    retryable: bool = False
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SourceQuality(BaseModel):
    url: str
    title: str | None = None
    quality_score: float | None = None
    extraction_strategy: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ConflictRecord(BaseModel):
    statement: str | None = None
    conflicting_statement: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ResultMemoryReference(BaseModel):
    id: str | None = None
    scope: str | None = None
    kind: str | None = None
    content: str | None = None
    confidence: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuditReferences(BaseModel):
    evidence_pack_artifact_id: str | None = None
    evidence_ledger_artifact_id: str | None = None
    evidence_record_count: int = 0
    agent_turn_count: int = 0
    referenced_artifact_ids: list[str] = Field(default_factory=list)


class RunError(BaseModel):
    type: str
    code: str
    message: str
    retryable: bool = False
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    """Stable API boundary for persisted runner result JSON."""

    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    answer_mode: AnswerMode = AnswerMode.trusted
    assurance_level: AssuranceLevel = AssuranceLevel.full
    findings: list[Finding] = Field(default_factory=list)
    claims: list[GroundingClaim] = Field(default_factory=list)
    citations: list[GroundingCitation] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    failed_sources: list[FailedSource] = Field(default_factory=list)
    source_quality: list[SourceQuality] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    verification_notes: list[str] = Field(default_factory=list)
    memory_references: list[ResultMemoryReference] = Field(default_factory=list)
    audit_refs: AuditReferences = Field(default_factory=AuditReferences)
    verification_report: VerificationReport | None = None
    completion_decision: CompletionDecision | None = None
    error: RunError | None = None
