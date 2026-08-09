from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.domain.memory import MemoryNamespaceType


class ConsolidationJobTrigger(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace_type: MemoryNamespaceType
    namespace_id: str = Field(min_length=1, max_length=120)
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
    )


class ConsolidationJobAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_state_version: int = Field(ge=1)
    actor: str | None = Field(default="local-operator", max_length=120)
    reason: str | None = Field(default=None, max_length=2_000)


class ConsolidationJobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    namespace_type: str
    namespace_id: str
    status: str
    state_version: int
    generation: int
    idempotency_key: str
    input_hash: str | None
    input_manifest: dict[str, Any]
    proposal: dict[str, Any]
    validation: dict[str, Any]
    profile_snapshot: dict[str, Any]
    model_usage: dict[str, Any]
    publish_result: dict[str, Any]
    error: dict[str, Any] | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    rollback_of_id: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    published_at: datetime | None


class ConsolidationJobList(BaseModel):
    jobs: list[ConsolidationJobView]
