from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.common.schemas.agent.planning import (
    ExpectedObservation,
    NodeExecutionView,
    ParallelismSummary,
    PlanVersionSummary,
    PlanView,
)
from app.common.schemas.agent.run_policy import RequestedReasoningPolicy
from app.common.schemas.agent.run_result import AgentRunResult
from app.common.schemas.agent.tool_invocation import PendingApprovalView
from app.common.schemas.agent.types import AnswerMode, ContinuationAction, PlanExecution, RuntimeKind
from app.common.schemas.models import RunModelConfig

SKILL_QUALIFIED_IDENTITY_RE = re.compile(
    r"^(?:builtin|custom):[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
)


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=4000)
    task_id: str | None = None
    session_id: str | None = Field(default=None, min_length=1, max_length=120)
    answer_mode: AnswerMode = AnswerMode.standard
    plan_execution: PlanExecution | None = None
    reasoning_policy: RequestedReasoningPolicy = Field(default_factory=RequestedReasoningPolicy)
    model: RunModelConfig | None = None
    interactive: bool = True
    permission_bundle: dict[str, Any] | None = None
    skill_ids: list[str] = Field(default_factory=list, max_length=8)
    subagent_mode: Literal["auto", "required"] = "auto"

    @field_validator("skill_ids")
    @classmethod
    def validate_skill_ids(cls, identities: list[str]) -> list[str]:
        if len(identities) != len(set(identities)):
            raise ValueError("skill_ids must contain unique qualified identities")
        if any(
            not SKILL_QUALIFIED_IDENTITY_RE.fullmatch(identity) or "--" in identity
            for identity in identities
        ):
            raise ValueError("skill_ids must contain valid qualified identities")
        return identities

    @model_validator(mode="after")
    def validate_plan_execution(self) -> CreateRunRequest:
        if self.subagent_mode == "required" and self.answer_mode == AnswerMode.standard:
            self.answer_mode = AnswerMode.trusted
            self.plan_execution = PlanExecution.auto
        if self.answer_mode == AnswerMode.standard and self.plan_execution is not None:
            raise ValueError("plan_execution is only valid for trusted runs")
        return self


class CreateRunResponse(BaseModel):
    task_id: str
    run_id: str
    status: str
    answer_mode: AnswerMode


class ContinueRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str | None = Field(default=None, max_length=4000)
    approved: bool | None = None
    action: ContinuationAction | None = None
    continuation_token: str | None = None
    plan_id: str | None = None
    expected_plan_version: int | None = Field(default=None, ge=1)
    expected_state_version: int | None = Field(default=None, ge=1)
    model: RunModelConfig | None = None

    @model_validator(mode="after")
    def validate_continuation(self) -> ContinueRunRequest:
        if self.action in {ContinuationAction.execute_plan, ContinuationAction.revise_plan}:
            required = (
                self.continuation_token,
                self.plan_id,
                self.expected_plan_version,
                self.expected_state_version,
            )
            if any(value is None for value in required):
                raise ValueError("plan continuation requires bound continuation fields")
            if self.action == ContinuationAction.revise_plan and (
                not self.content or not self.content.strip()
            ):
                raise ValueError("content is required for plan revision")
            return self
        if not self.content or not self.content.strip():
            raise ValueError("content is required for user response")
        return self


class StepView(BaseModel):
    id: str
    plan_id: str | None = None
    plan_version: int | None = None
    node_key: str | None = None
    index: int
    title: str
    intent: str
    status: str
    depends_on: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    success_criteria_refs: list[str] = Field(default_factory=list)
    expected_outcome: ExpectedObservation | None = None
    risk_level: str = "low"
    optional: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    failure: dict[str, Any] | None = None
    evidence: dict[str, Any] | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ToolCallView(BaseModel):
    id: str
    step_id: str | None
    plan_node_id: str | None = None
    node_execution_id: str | None = None
    tool_name: str
    tool_version: str
    input: dict[str, Any]
    output: dict[str, Any] | None
    status: str
    permission: str
    side_effect_level: str
    started_at: datetime
    completed_at: datetime | None
    error: dict[str, Any] | None


class ArtifactView(BaseModel):
    id: str
    type: str
    path: str | None
    content_ref: str | None
    metadata: dict[str, Any]
    mime_type: str | None = None
    size_bytes: int = 0
    checksum: str | None = None
    security_status: str = "pending"
    tool_call_id: str | None = None
    plan_node_id: str | None = None
    sandbox_job_id: str | None = None
    provenance: dict[str, Any] = Field(default_factory=dict)
    content_url: str | None = None
    created_at: datetime


class RunEventView(BaseModel):
    id: int
    run_sequence: int | None = None
    agent_execution_id: str | None = None
    agent_sequence: int | None = None
    type: str
    payload: dict[str, Any]
    created_at: datetime


class AgentTurnView(BaseModel):
    id: str
    run_id: str
    plan_node_id: str | None = None
    node_execution_id: str | None = None
    turn_index: int
    decision_type: str
    reasoning_summary: str
    selected_tool: str | None = None
    decision: dict[str, Any]
    observation: dict[str, Any] | None
    reflection: dict[str, Any] | None
    tool_call_id: str | None
    artifact_id: str | None
    memory_reads: list[dict[str, Any]]
    memory_writes: list[dict[str, Any]]
    status: str
    evaluation: dict[str, Any] | None = None
    reflection_patch: dict[str, Any] | None = None
    state_version_before: int | None = None
    state_version_after: int | None = None
    plan_version: int = 1
    phase: str = "created"
    idempotency_key: str | None = None
    paused_node: str | None = None
    created_at: datetime
    updated_at: datetime


class AgentRunMemoryView(BaseModel):
    id: str
    run_id: str | None
    memory_key: str
    namespace_type: str
    namespace_id: str
    scope: str
    kind: str
    status: str
    version: int
    state_version: int
    content: str
    structured_data: dict[str, Any]
    provenance: dict[str, Any]
    confidence: float
    importance: float
    utility_score: float
    access_count: int
    observed_at: datetime
    valid_from: datetime
    valid_to: datetime | None
    supersedes_id: str | None
    consolidation_generation: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None
    last_accessed_at: datetime | None
    revoked_at: datetime | None
    revoke_reason: str | None


class ChatMessageView(BaseModel):
    id: str
    role: str
    content: str
    status: str = "completed"
    metadata: dict[str, Any] = Field(default_factory=dict)


class SandboxJobView(BaseModel):
    id: str
    tool_call_id: str | None = None
    status: str
    executor: str
    runtime_profile: dict[str, Any]
    resource_limits: dict[str, Any]
    runtime_name: str | None = None
    image_digest: str | None = None
    exit_reason: str | None = None
    error: dict[str, Any] | None = None
    stdout_summary: str | None = None
    stderr_summary: str | None = None
    input_artifact_ids: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class AgentExecutionView(BaseModel):
    id: str
    parent_execution_id: str | None = None
    execution_type: str
    identity_id: str | None = None
    delegation_id: str | None = None
    request_id: str
    depth: int
    ordinal: int
    objective: str | None = None
    creation_reason: str | None = None
    required: bool = True
    status: str
    phase: str
    wait_reason: str | None = None
    budget_envelope: dict[str, Any] = Field(default_factory=dict)
    budget_usage: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    result_summary: str | None = None
    open_issues: list[str] = Field(default_factory=list)
    error: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None
    plan: PlanView | None = None
    children: list[AgentExecutionView] = Field(default_factory=list)


class SubagentSummaryView(BaseModel):
    total: int = 0
    running: int = 0
    waiting: int = 0
    completed: int = 0
    failed: int = 0
    cancelled: int = 0
    budget_usage: dict[str, float] = Field(default_factory=dict)
    key_wait_reason: str | None = None


class AgentJoinView(BaseModel):
    id: str
    parent_execution_id: str
    consumer_plan_node_id: str | None = None
    join_key: str
    group_id: str | None = None
    policy: str
    child_execution_ids: list[str] = Field(default_factory=list)
    required_execution_ids: list[str] = Field(default_factory=list)
    optional_execution_ids: list[str] = Field(default_factory=list)
    status: str
    result: dict[str, Any] = Field(default_factory=dict)
    state_version: int
    created_at: datetime
    completed_at: datetime | None = None
    updated_at: datetime


class RunView(BaseModel):
    id: str
    task_id: str
    status: str
    mode: str
    processing_duration_ms: int | None = None
    answer_mode: AnswerMode = AnswerMode.trusted
    runtime_kind: RuntimeKind = RuntimeKind.trusted_v1
    runtime_version: int = 1
    fast_runtime_snapshot: dict[str, Any] = Field(default_factory=dict)
    fast_state_version: int = 0
    execution_profile: dict[str, Any] = Field(default_factory=dict)
    summary: str | None
    result: AgentRunResult | None
    steps: list[StepView]
    tool_calls: list[ToolCallView]
    artifacts: list[ArtifactView]
    sandbox_jobs: list[SandboxJobView] = Field(default_factory=list)
    events: list[RunEventView]
    turns: list[AgentTurnView] = Field(default_factory=list)
    memories: list[AgentRunMemoryView] = Field(default_factory=list)
    chat_messages: list[ChatMessageView] = Field(default_factory=list)
    model_policy: dict[str, Any] = Field(default_factory=dict)
    reasoning_policy: dict[str, Any] = Field(default_factory=dict)
    task_contract: dict[str, Any] = Field(default_factory=dict)
    plan_graph: PlanView | dict[str, Any] = Field(default_factory=dict)
    plan_versions: list[PlanVersionSummary] = Field(default_factory=list)
    agent_state: dict[str, Any] = Field(default_factory=dict)
    state_version: int = 0
    terminal_reason: dict[str, Any] | None = None
    waiting_state: dict[str, Any] | None = None
    pending_approval: PendingApprovalView | None = None
    node_executions: list[NodeExecutionView] = Field(default_factory=list)
    parallelism: ParallelismSummary | None = None
    agent_executions: list[AgentExecutionView] = Field(default_factory=list)
    agent_joins: list[AgentJoinView] = Field(default_factory=list)
    subagent_summary: SubagentSummaryView = Field(default_factory=SubagentSummaryView)
    task_adapter: str = "web"
    agent_profile: dict[str, Any] = Field(default_factory=dict)


# FastAPI disambiguates the API AgentRunMemoryView from the memory-domain view with
# this historical module name. Preserve it so schema splitting is HTTP-neutral.
AgentRunMemoryView.__module__ = "app.common.schemas.agent"
AgentRunMemoryView.model_rebuild(force=True)
RunView.model_rebuild(force=True)
