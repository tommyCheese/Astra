from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.common.schemas.agent.execution_state import CompletionDecision
from app.common.schemas.agent.types import AnswerMode, AssuranceLevel
from app.domain.grounding.schemas import GroundedAnswerCitation, GroundedAnswerClaim


class AgentAnswerSourceReference(BaseModel):
    url: str
    title: str | None = None
    retrieved_at: str | None = None


class AgentAnswerFinding(BaseModel):
    text: str
    source_urls: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class AgentFinalAnswer(BaseModel):
    summary: str
    findings: list[AgentAnswerFinding] = Field(default_factory=list)
    claims: list[GroundedAnswerClaim] = Field(default_factory=list)
    citations: list[GroundedAnswerCitation] = Field(default_factory=list)
    sources: list[AgentAnswerSourceReference] = Field(default_factory=list)
    failed_sources: list[dict[str, Any]] = Field(default_factory=list)
    source_quality: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    verification_notes: list[str] = Field(default_factory=list)
    memory_references: list[dict[str, Any]] = Field(default_factory=list)
    audit_refs: dict[str, Any] = Field(default_factory=dict)


class AgentRunMemoryCandidate(BaseModel):
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


class AgentValidationIssue(BaseModel):
    code: str
    message: str
    severity: str = "error"
    evidence_refs: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AgentValidationOutcome(BaseModel):
    validator: str
    passed: bool
    blocking: bool = True
    requirement_ids: list[str] = Field(default_factory=list)
    issues: list[AgentValidationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class AgentAnswerVerificationReport(BaseModel):
    status: str
    assurance_level: AssuranceLevel = AssuranceLevel.full
    source_count: int = 0
    caveat_count: int = 0
    low_quality_sources: list[dict[str, Any]] = Field(default_factory=list)
    failed_sources: list[dict[str, Any]] = Field(default_factory=list)
    memory_references: list[dict[str, Any]] = Field(default_factory=list)
    invalid_artifact_references: int = 0
    notes: list[str] = Field(default_factory=list)
    validation_outcomes: list[AgentValidationOutcome] = Field(default_factory=list)


class AgentAnswerFailedSource(BaseModel):
    url: str | None = None
    title: str | None = None
    type: str | None = None
    category: str | None = None
    code: str | None = None
    message: str | None = None
    retryable: bool = False
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AgentAnswerSourceQuality(BaseModel):
    url: str
    title: str | None = None
    quality_score: float | None = None
    extraction_strategy: str | None = None
    warnings: list[str] = Field(default_factory=list)


class AgentAnswerConflictRecord(BaseModel):
    statement: str | None = None
    conflicting_statement: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class AgentAnswerMemoryReference(BaseModel):
    id: str | None = None
    scope: str | None = None
    kind: str | None = None
    content: str | None = None
    confidence: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AgentRunAuditReferences(BaseModel):
    evidence_pack_artifact_id: str | None = None
    evidence_ledger_artifact_id: str | None = None
    evidence_record_count: int = 0
    agent_turn_count: int = 0
    referenced_artifact_ids: list[str] = Field(default_factory=list)


class AgentRunError(BaseModel):
    type: str
    code: str
    message: str
    retryable: bool = False
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AgentRunResult(BaseModel):
    """Stable API boundary for persisted runner result JSON."""

    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    answer_mode: AnswerMode = AnswerMode.trusted
    assurance_level: AssuranceLevel = AssuranceLevel.full
    findings: list[AgentAnswerFinding] = Field(default_factory=list)
    claims: list[GroundedAnswerClaim] = Field(default_factory=list)
    citations: list[GroundedAnswerCitation] = Field(default_factory=list)
    sources: list[AgentAnswerSourceReference] = Field(default_factory=list)
    failed_sources: list[AgentAnswerFailedSource] = Field(default_factory=list)
    source_quality: list[AgentAnswerSourceQuality] = Field(default_factory=list)
    conflicts: list[AgentAnswerConflictRecord] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    verification_notes: list[str] = Field(default_factory=list)
    memory_references: list[AgentAnswerMemoryReference] = Field(default_factory=list)
    audit_refs: AgentRunAuditReferences = Field(default_factory=AgentRunAuditReferences)
    verification_report: AgentAnswerVerificationReport | None = None
    completion_decision: CompletionDecision | None = None
    error: AgentRunError | None = None
