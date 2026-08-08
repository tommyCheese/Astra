from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GroundingEvidenceKind(str, Enum):
    search_trace = "search_trace"
    search_candidate = "search_candidate"
    source_snapshot = "source_snapshot"
    passage = "passage"
    source_failure = "source_failure"
    claim = "claim"
    support_edge = "support_edge"
    citation = "citation"


class GroundingSourcePassage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_id: str
    snapshot_id: str
    ordinal: int = Field(ge=0)
    text: str
    start_offset: int = Field(ge=0)
    end_offset: int = Field(ge=0)
    section: str | None = None
    evidence_strength: Literal["source_passage"] = "source_passage"

    @model_validator(mode="after")
    def validate_offsets(self) -> GroundingSourcePassage:
        if self.end_offset < self.start_offset:
            raise ValueError("passage end_offset precedes start_offset")
        return self


class GroundedAnswerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    evidence_refs: list[str] = Field(default_factory=list)
    material: bool = True
    support_status: Literal["unverified", "supported", "unsupported"] = "unverified"


class GroundedAnswerCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    claim_id: str
    evidence_ref: str
    source_id: str | None = None
    passage_id: str | None = None
    url: str | None = None
    title: str | None = None
    ordinal: int | None = Field(default=None, ge=1)


class GroundingEvidenceLineage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str | None = None
    plan_node_id: str | None = None
    node_execution_id: str | None = None
    tool_call_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)


class GroundingEvidenceFragment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    kind: GroundingEvidenceKind
    evidence_key: str
    payload_digest: str
    payload: dict[str, Any]
    lineage: GroundingEvidenceLineage = Field(default_factory=GroundingEvidenceLineage)
    created_at: str = Field(default_factory=utc_iso)
