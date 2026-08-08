from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PermissionDecisionKind(str, Enum):
    allow = "allow"
    ask = "ask"
    deny = "deny"


class PermissionScope(str, Enum):
    once = "once"
    run = "run"
    task = "task"


class PolicyTier(str, Enum):
    platform = "platform"
    managed = "managed"
    deployment = "deployment"
    user = "user"
    task = "task"
    run = "run"
    once = "once"


class PermissionSubject(BaseModel):
    agent_id: str
    identity_type: str = "agent"
    user_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    parent_agent_id: str | None = None
    agent_execution_id: str | None = None
    delegation_id: str | None = None
    delegation_chain: list[str] = Field(default_factory=list)


class PermissionConditions(BaseModel):
    tool_name: str | None = None
    tool_version: str | None = None
    provider_id: str | None = None
    schema_digest: str | None = None
    analyzer_version: str | None = None
    working_directory: str | None = None
    network_destination: str | None = None
    data_labels: list[str] = Field(default_factory=list)
    interactive: bool = True
    constraints: dict[str, Any] = Field(default_factory=dict)


class PermissionRequest(BaseModel):
    id: str | None = None
    subject: PermissionSubject
    action: str
    resource: str
    conditions: PermissionConditions = Field(default_factory=PermissionConditions)
    effect_plan_hash: str | None = None
    requested_at: datetime | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class PermissionRule(BaseModel):
    id: str
    source: str
    tier: PolicyTier
    decision: PermissionDecisionKind
    actions: list[str] = Field(default_factory=lambda: ["*"])
    resources: list[str] = Field(default_factory=lambda: ["*"])
    conditions: dict[str, Any] = Field(default_factory=dict)
    reason_code: str
    enabled: bool = True
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PermissionPolicySet(BaseModel):
    version: str
    rules: list[PermissionRule] = Field(default_factory=list)
    source_digests: dict[str, str] = Field(default_factory=dict)


class PolicyMatch(BaseModel):
    policy_id: str
    source: str
    tier: str
    decision: PermissionDecisionKind
    reason_code: str
    constraints: dict[str, Any] = Field(default_factory=dict)


class PolicyExplanation(BaseModel):
    reason_code: str
    summary: str
    matched_policies: list[PolicyMatch] = Field(default_factory=list)
    enforced_scope: dict[str, Any] = Field(default_factory=dict)
    trace: list[str] = Field(default_factory=list)


class PermissionDecision(BaseModel):
    decision: PermissionDecisionKind
    explanation: PolicyExplanation
    grant_proposals: list[GrantProposal] = Field(default_factory=list)
    decided_at: datetime | None = None


class EffectKind(str, Enum):
    workspace_read = "workspace_read"
    workspace_write = "workspace_write"
    workspace_delete = "workspace_delete"
    artifact_write = "artifact_write"
    dependency_change = "dependency_change"
    temporary_compute = "temporary_compute"
    process_execute = "process_execute"
    process_execute_unknown = "process_execute_unknown"
    network_read = "network_read"
    network_write = "network_write"
    external_write = "external_write"
    sensitive_data_read = "sensitive_data_read"
    credential_use = "credential_use"
    delegation_create = "delegation_create"
    permission_change = "permission_change"
    memory_write = "memory_write"
    memory_delete = "memory_delete"


class EffectItem(BaseModel):
    kind: EffectKind
    resource: str
    risk: str = "low"
    reversible: bool = True
    persistent: bool = False
    data_labels: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ActionEffectPlan(BaseModel):
    tool_name: str
    tool_version: str
    summary: str
    cwd: str | None = None
    effects: list[EffectItem] = Field(default_factory=list)
    required_permissions: list[str] = Field(default_factory=list)
    network_scope: dict[str, Any] = Field(default_factory=dict)
    analyzer_version: str
    analyzer_digest: str | None = None
    approval_required: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_effects_for_approval(self) -> ActionEffectPlan:
        if self.approval_required and not self.effects:
            raise ValueError("approval_required effect plans must contain at least one effect")
        return self


class GrantProposal(BaseModel):
    scope: PermissionScope
    label: str
    effect_kinds: list[EffectKind] = Field(default_factory=list)
    resource_matcher: dict[str, Any] = Field(default_factory=dict)
    invocation_constraints: dict[str, Any] = Field(default_factory=dict)
    expires_at: datetime | None = None
    max_uses: int | None = Field(default=None, ge=1)


class PermissionBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    version: str
    allowed_actions: list[str] = Field(default_factory=list)
    allowed_resources: list[str] = Field(default_factory=list)
    allowed_effect_kinds: list[EffectKind] = Field(default_factory=list)
    allowed_tool_identities: list[str] = Field(default_factory=list)
    network_destinations: list[str] = Field(default_factory=list)
    allowed_data_labels: list[str] = Field(default_factory=list)
    allowed_credential_scopes: list[str] = Field(default_factory=list)
    output_destinations: list[str] = Field(default_factory=list)
    max_tool_calls: int | None = Field(default=None, ge=1)
    max_runtime_seconds: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None
    digest: str


class PolicySimulationRequest(BaseModel):
    request: PermissionRequest
    policies: PermissionPolicySet
    shadow_policies: PermissionPolicySet | None = None


class PolicySimulationResult(BaseModel):
    effective: PermissionDecision
    shadow: PermissionDecision | None = None
    changed: bool = False


class ExtensionDescriptor(BaseModel):
    extension_type: str
    id: str
    version: str
    provider_id: str
    digest: str
    trust_level: str
    enabled: bool = True
    schema_digest: str | None = None
    annotations: dict[str, Any] = Field(default_factory=dict)


PermissionDecision.model_rebuild()
