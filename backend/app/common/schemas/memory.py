from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MemorySourceView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_kind: str
    source_ref: str
    source_hash: str | None = None
    run_id: str | None = None
    turn_id: str | None = None
    tool_call_id: str | None = None
    artifact_id: str | None = None
    accessible: bool
    source_data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    revoked_at: datetime | None = None


class MemoryAuditView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    event_type: str
    actor: str | None = None
    reason: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class MemoryRecallView(BaseModel):
    event_id: str
    run_id: str | None = None
    turn_id: str | None = None
    query_fingerprint: str
    policy_version: str
    selected: bool
    exclusion_reason: str | None = None
    scores: dict[str, float | None] = Field(default_factory=dict)
    feedback: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None


class MemoryView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str | None = None
    created_by: str | None = None
    memory_key: str
    namespace_type: str
    namespace_id: str
    scope: str
    kind: str
    status: str
    version: int
    state_version: int
    content: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    confidence: float
    importance: float
    utility_score: float
    access_count: int
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes_id: str | None = None
    consolidation_generation: int
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    last_accessed_at: datetime | None = None
    revoked_at: datetime | None = None
    revoke_reason: str | None = None


class MemoryDetailView(MemoryView):
    sources: list[MemorySourceView] = Field(default_factory=list)
    recall_events: list[MemoryRecallView] = Field(default_factory=list)
    audit_events: list[MemoryAuditView] = Field(default_factory=list)
    history: list[MemoryView] = Field(default_factory=list)


class MemoryListView(BaseModel):
    items: list[MemoryView]
    total: int
    next_cursor: str | None = None


class MemoryRevocationRequest(BaseModel):
    expected_state_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2_000)
    actor: str = Field(default="local-admin", min_length=1, max_length=120)


class MemoryActivationRequest(BaseModel):
    expected_state_version: int = Field(ge=1)
    reason: str = Field(min_length=3, max_length=2_000)
    actor: str = Field(default="local-operator", min_length=1, max_length=120)


class MemoryRecallFeedbackRequest(BaseModel):
    outcome: str
    utility_delta: float = Field(ge=-1.0, le=1.0)
    details: dict[str, Any] = Field(default_factory=dict)
