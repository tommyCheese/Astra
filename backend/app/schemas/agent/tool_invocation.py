from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.schemas.agent.types import ApprovalDecision
from app.schemas.models import RunModelConfig


class ApprovalDecisionRequest(BaseModel):
    decision: ApprovalDecision
    continuation_token: str
    model: RunModelConfig | None = None
    guidance: str | None = Field(default=None, max_length=1000)


class PendingApprovalView(BaseModel):
    id: str
    tool_call_id: str
    node_execution_id: str | None = None
    execution_attempt: int | None = None
    expected_execution_state_version: int | None = None
    tool_name: str
    preview: str
    permission: str
    impact: str
    action_summary: str | None = None
    affected_resources: list[str] = Field(default_factory=list)
    risk_reason: str | None = None
    working_directory: str | None = None
    network_scope: dict[str, Any] = Field(default_factory=dict)
    effect_kinds: list[str] = Field(default_factory=list)
    grant_proposals: list[dict[str, Any]] = Field(default_factory=list)
    reviewer_identity: dict[str, Any] | None = None
    decisions: list[ApprovalDecision]
    created_at: datetime


class BashExecuteResult(BaseModel):
    exit_code: int
    stdout: str = ""
    stderr: str = ""
