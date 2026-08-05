"""Typed commands accepted by approval persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ApprovalRequestCreate:
    run_id: str
    turn_id: str
    tool_call_id: str
    tool_name: str
    tool_version: str
    frozen_input: dict[str, Any]
    input_hash: str
    preview: str
    permission: str
    impact: str
    similar_matcher: dict[str, Any] | None
    frozen_effect_plan: dict[str, Any] | None = None
    effect_plan_hash: str | None = None
    analyzer_version: str | None = None
    analyzer_digest: str | None = None
    reviewer_identity: dict[str, Any] | None = None
    agent_execution_id: str | None = None
    requester_identity_id: str | None = None
    delegation_id: str | None = None
    catalog_digest: str | None = None
    continuation_token: str | None = None
    grant_scope: dict[str, Any] | None = None
    node_execution_id: str | None = None
    execution_attempt: int | None = None
    expected_execution_state_version: int | None = None
