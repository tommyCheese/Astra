from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class DelegationRejectionCode(str, Enum):
    feature_disabled = "subagent_feature_disabled"
    kill_switch_active = "subagent_kill_switch_active"
    incomplete_scope = "delegation_incomplete_scope"
    missing_success_criteria = "delegation_missing_success_criteria"
    invalid_output_schema = "delegation_invalid_output_schema"
    disallowed_join_policy = "delegation_join_policy_disallowed"
    excessive_depth = "delegation_depth_exceeded"
    duplicate_request = "delegation_duplicate_request"
    budget_rejected = "delegation_budget_rejected"
    not_beneficial = "delegation_not_beneficial"
    capability_not_delegated = "delegation_capability_not_delegated"
    resource_not_delegated = "delegation_resource_not_delegated"
    scope_overlap = "delegation_scope_overlap"
    catalog_drift = "delegation_catalog_drift"
    context_dropped = "delegation_execution_context_dropped"
    identity_overlap = "delegation_identity_overlap"
    fanout_too_large = "delegation_fanout_too_large"
    fanout_conflict = "delegation_fanout_conflict"


class DelegationValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: DelegationRejectionCode
    message: str
    field: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SubagentJoinPolicy(str, Enum):
    required = "required"
    optional = "optional"
    first_success = "first_success"


class SubagentExecutionStatus(str, Enum):
    proposed = "proposed"
    authorizing = "authorizing"
    queued = "queued"
    running = "running"
    waiting_parent = "waiting_parent"
    waiting_approval = "waiting_approval"
    waiting_resource = "waiting_resource"
    completing = "completing"
    completed = "completed"
    completed_with_warnings = "completed_with_warnings"
    blocked = "blocked"
    failed = "failed"
    cancelled = "cancelled"


class SubagentExecutionPhase(str, Enum):
    proposed = "proposed"
    authorizing = "authorizing"
    claimed = "claimed"
    planning = "planning"
    executing = "executing"
    waiting_parent = "waiting_parent"
    waiting_approval = "waiting_approval"
    waiting_resource = "waiting_resource"
    checkpointing = "checkpointing"
    completing = "completing"
    terminal = "terminal"


class DelegationScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    included: list[str] = Field(min_length=1)
    excluded: list[str] = Field(default_factory=list)

    @field_validator("included", "excluded")
    @classmethod
    def normalize_scope_items(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError(
                f"{DelegationRejectionCode.incomplete_scope.value}: scope entries must be unique"
            )
        return normalized


class SubagentBudgetEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_tokens: int = Field(default=8_000, ge=1)
    max_model_calls: int = Field(default=4, ge=1)
    max_tool_calls: int = Field(default=8, ge=0)
    max_wall_time_ms: int = Field(default=120_000, ge=1_000)
    max_cost_usd: float = Field(default=0.5, ge=0)
    max_children: int = Field(default=0, ge=0)
    max_parallel_nodes: int = Field(default=1, ge=1)


class DelegationInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["fact", "artifact", "evidence", "structured_data"]
    ref: str = Field(min_length=1, max_length=1_000)
    summary: str | None = Field(default=None, max_length=2_000)
    content_hash: str | None = Field(default=None, max_length=160)
    data_labels: list[str] = Field(default_factory=list)
    allowed_purposes: list[str] = Field(default_factory=list)


class DelegationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=160)
    objective: str = Field(min_length=1, max_length=4_000)
    success_criteria: list[str] = Field(min_length=1, max_length=32)
    scope: DelegationScope
    inputs: list[DelegationInput] = Field(default_factory=list, max_length=64)
    output_schema: dict[str, Any]
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)
    requested_tools: list[str] = Field(default_factory=list, max_length=32)
    requested_skills: list[str] = Field(default_factory=list, max_length=16)
    resource_scope: dict[str, Any] = Field(default_factory=dict)
    budget: SubagentBudgetEnvelope = Field(default_factory=SubagentBudgetEnvelope)
    deadline_at: datetime | None = None
    join_policy: SubagentJoinPolicy = SubagentJoinPolicy.required
    dedupe_key: str = Field(min_length=1, max_length=240)
    relationship: Literal["work", "independent_review"] = "work"

    @field_validator("objective")
    @classmethod
    def normalize_objective(cls, value: str) -> str:
        return value.strip()

    @field_validator("success_criteria")
    @classmethod
    def validate_success_criteria(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if not normalized:
            raise ValueError(
                f"{DelegationRejectionCode.missing_success_criteria.value}: "
                "at least one success criterion is required"
            )
        if len(normalized) != len(set(normalized)):
            raise ValueError(
                f"{DelegationRejectionCode.missing_success_criteria.value}: "
                "success criteria must be unique"
            )
        return normalized

    @field_validator("required_capabilities", "requested_tools", "requested_skills")
    @classmethod
    def validate_unique_identifiers(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("delegation identifiers must be unique")
        return normalized

    @field_validator("output_schema")
    @classmethod
    def validate_output_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        if value.get("type") != "object":
            raise ValueError(
                f"{DelegationRejectionCode.invalid_output_schema.value}: "
                "output_schema.type must be object"
            )
        properties = value.get("properties")
        if properties is not None and not isinstance(properties, dict):
            raise ValueError(
                f"{DelegationRejectionCode.invalid_output_schema.value}: "
                "output_schema.properties must be an object"
            )
        required = value.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise ValueError(
                f"{DelegationRejectionCode.invalid_output_schema.value}: "
                "output_schema.required must be a string array"
            )
        if properties is not None and any(item not in properties for item in required):
            raise ValueError(
                f"{DelegationRejectionCode.invalid_output_schema.value}: "
                "required output fields must be declared in properties"
            )
        return value

    @model_validator(mode="after")
    def validate_deadline(self) -> DelegationRequest:
        if self.deadline_at is not None and self.deadline_at.tzinfo is None:
            raise ValueError("delegation deadline_at must include a timezone")
        return self


class SubagentJoinSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    key: str = Field(min_length=1, max_length=160)
    policy: SubagentJoinPolicy = SubagentJoinPolicy.required
    consumer_plan_node_id: str | None = Field(default=None, max_length=160)


class SubagentFanoutRequest(BaseModel):
    """One idempotent Swarm request containing a bounded child group."""

    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1, max_length=160)
    tasks: list[DelegationRequest] = Field(min_length=1, max_length=16)
    join: SubagentJoinSpec

    @model_validator(mode="after")
    def validate_group(self) -> SubagentFanoutRequest:
        request_ids = [item.request_id for item in self.tasks]
        dedupe_keys = [item.dedupe_key for item in self.tasks]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("fan-out request ids must be unique")
        if len(dedupe_keys) != len(set(dedupe_keys)):
            raise ValueError("fan-out dedupe keys must be unique")
        return self


class SubagentFanoutResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["accepted"] = "accepted"
    group_id: str
    join_id: str
    child_execution_ids: tuple[str, ...]
    idempotent_replay: bool = False


class DelegationContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    contract_id: str = Field(min_length=1, max_length=160)
    contract_hash: str = Field(min_length=1, max_length=160)
    task_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=160)
    parent_execution_id: str = Field(min_length=1, max_length=160)
    depth: int = Field(ge=1)
    request: DelegationRequest
    created_at: datetime


class EffectiveDelegationScope(BaseModel):
    """The fully attenuated authority carried by a child identity."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    actions: tuple[str, ...] = ()
    resources: tuple[str, ...] = ()
    effect_kinds: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    credential_scopes: tuple[str, ...] = ()
    data_labels: tuple[str, ...] = ()
    allowed_purposes: tuple[str, ...] = ()
    network_destinations: tuple[str, ...] = ()
    workspace_read_roots: tuple[str, ...] = ()
    workspace_write_roots: tuple[str, ...] = ()
    private_staging_root: str
    max_uses: int | None = Field(default=None, ge=1)
    max_tool_calls: int | None = Field(default=None, ge=0)
    max_runtime_seconds: int | None = Field(default=None, ge=1)


class DelegatedExecutionContext(BaseModel):
    """Fail-closed context required at every child tool authorization boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    task_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    agent_execution_id: str = Field(min_length=1)
    identity_id: str = Field(min_length=1)
    parent_identity_id: str = Field(min_length=1)
    delegation_id: str = Field(min_length=1)
    delegation_chain: tuple[str, ...] = Field(min_length=2)
    purpose: str = Field(min_length=1, max_length=1_000)
    effective_scope: EffectiveDelegationScope
    budget_envelope: SubagentBudgetEnvelope
    budget_usage: dict[str, int | float] = Field(default_factory=dict)
    data_flow_state: dict[str, Any] = Field(default_factory=dict)
    workspace_scope: dict[str, Any]
    tool_catalog_digest: str = Field(min_length=1, max_length=160)
    skill_catalog_digest: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_identity_chain(self) -> DelegatedExecutionContext:
        if self.delegation_chain[-1] != self.identity_id:
            raise ValueError(
                f"{DelegationRejectionCode.context_dropped.value}: "
                "delegation chain must terminate at the child identity"
            )
        if self.parent_identity_id not in self.delegation_chain[:-1]:
            raise ValueError(
                f"{DelegationRejectionCode.context_dropped.value}: "
                "delegation chain must contain the parent identity"
            )
        if self.effective_scope.allowed_purposes and not any(
            self.purpose == allowed
            for allowed in self.effective_scope.allowed_purposes
        ):
            raise ValueError(
                f"{DelegationRejectionCode.context_dropped.value}: "
                "execution purpose is outside the delegated scope"
            )
        expected_workspace = {
            "read_roots": list(self.effective_scope.workspace_read_roots),
            "write_roots": list(self.effective_scope.workspace_write_roots),
            "private_staging_root": self.effective_scope.private_staging_root,
        }
        if self.workspace_scope != expected_workspace:
            raise ValueError(
                f"{DelegationRejectionCode.context_dropped.value}: "
                "workspace scope does not match the effective delegation"
            )
        budget_limits = self.budget_envelope.model_dump()
        usage_to_limit = {
            "tokens": "max_tokens",
            "model_calls": "max_model_calls",
            "tool_calls": "max_tool_calls",
            "wall_time_ms": "max_wall_time_ms",
            "cost_usd": "max_cost_usd",
        }
        if any(
            float(self.budget_usage.get(usage_key, 0))
            > float(budget_limits[limit_key])
            for usage_key, limit_key in usage_to_limit.items()
        ):
            raise ValueError(
                f"{DelegationRejectionCode.budget_rejected.value}: "
                "child budget usage exceeds its frozen envelope"
            )
        return self


class SubagentContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=160)
    kind: Literal[
        "profile",
        "role_protocol",
        "delegation_contract",
        "fact",
        "artifact",
        "evidence",
        "catalog",
        "workspace_view",
        "budget",
        "termination",
    ]
    ref: str | None = Field(default=None, max_length=1_000)
    content: str | None = Field(default=None, max_length=40_000)
    summary: str = Field(min_length=1, max_length=2_000)
    content_hash: str = Field(min_length=1, max_length=160)
    provenance: dict[str, Any] = Field(default_factory=dict)
    data_labels: list[str] = Field(default_factory=list)
    allowed_purposes: list[str] = Field(default_factory=list)
    estimated_tokens: int = Field(default=0, ge=0)
    size_bytes: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def require_reference_or_content(self) -> SubagentContextItem:
        if self.ref is None and self.content is None:
            raise ValueError("subagent context item requires ref or content")
        return self


class SubagentContextManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    agent_execution_id: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=4_000)
    items: tuple[SubagentContextItem, ...] = ()
    tool_catalog_digest: str | None = Field(default=None, max_length=160)
    skill_catalog_digest: str | None = Field(default=None, max_length=160)
    workspace_scope: dict[str, Any] = Field(default_factory=dict)
    total_estimated_tokens: int = Field(default=0, ge=0)
    created_at: datetime

    @model_validator(mode="after")
    def validate_token_total(self) -> SubagentContextManifest:
        item_total = sum(item.estimated_tokens for item in self.items)
        if self.total_estimated_tokens < item_total:
            raise ValueError("context token total cannot be smaller than item estimates")
        return self


class SubagentArtifactReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    uri: str
    name: str | None = None
    mime_type: str | None = None
    content_hash: str | None = None


class SubagentEvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    kind: str = "evidence"
    summary: str | None = None


class SubagentQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt: str = Field(min_length=1, max_length=4_000)
    required_fields: list[str] = Field(default_factory=list)
    continuation_token: str = Field(min_length=1, max_length=240)
    round_trip: int = Field(default=1, ge=1)


class SubagentContinuationAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    agent_execution_id: str = Field(min_length=1)
    continuation_token: str = Field(min_length=1, max_length=240)
    round_trip: int = Field(ge=1)
    values: dict[str, Any]
    answered_at: datetime


class SubagentContextGap(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    input_ref: str
    reason_code: Literal[
        "purpose_mismatch",
        "data_label_denied",
        "permission_denied",
        "inline_too_large",
        "token_budget_exceeded",
    ]
    summary: str


class SubagentContextCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    agent_execution_id: str
    manifest_hash: str
    local_summary: str = Field(max_length=16_000)
    local_facts: tuple[dict[str, Any], ...] = ()
    continuation_round_trips: int = Field(default=0, ge=0)
    continuation_answers: tuple[SubagentContinuationAnswer, ...] = ()
    created_at: datetime


class SubagentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    status: SubagentExecutionStatus
    summary: str = Field(default="", max_length=8_000)
    outputs: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[SubagentArtifactReference] = Field(default_factory=list)
    evidence_refs: list[SubagentEvidenceReference] = Field(default_factory=list)
    claims: list[dict[str, Any]] = Field(default_factory=list)
    open_issues: list[str] = Field(default_factory=list)
    question: SubagentQuestion | None = None
    completion: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, int | float] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_status_payload(self) -> SubagentResult:
        if self.status == SubagentExecutionStatus.waiting_parent and self.question is None:
            raise ValueError("waiting_parent subagent result requires a question")
        if self.status != SubagentExecutionStatus.waiting_parent and self.question is not None:
            raise ValueError("subagent question is only valid for waiting_parent")
        return self
