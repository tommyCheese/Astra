from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from app.common.schemas.subagents import SubagentContextCheckpoint, SubagentContinuationAnswer


class ContextOwnerRole(str, Enum):
    conversation = "conversation"
    root_execution = "root_execution"
    child_execution = "child_execution"


class ContextThresholdScope(str, Enum):
    total = "total"
    body_after_prefix = "body_after_prefix"


class CompactionImplementation(str, Enum):
    astra_semantic = "astra_semantic"
    deterministic_emergency = "deterministic_emergency"
    legacy_v1 = "legacy_v1"


class CompactionLifecycleStatus(str, Enum):
    projected = "projected"
    started = "started"
    completed = "completed"
    failed = "failed"
    superseded = "superseded"
    skipped = "skipped"


class TokenAccounting(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    context_window: int = Field(ge=1)
    output_reserve: int = Field(ge=0)
    compaction_output_reserve: int = Field(ge=0)
    usable_input: int = Field(ge=0)
    protected_prefix_tokens: int = Field(default=0, ge=0)
    checkpoint_tokens: int = Field(default=0, ge=0)
    body_tokens: int = Field(default=0, ge=0)
    recent_tail_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    prefill_tokens: int = Field(default=0, ge=0)
    source: str = Field(min_length=1, max_length=120)
    estimated: bool = True


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=240)
    kind: str = Field(min_length=1, max_length=80)
    content: Any = None
    summary: str | None = Field(default=None, max_length=8_000)
    reference: str | None = Field(default=None, max_length=1_000)
    content_hash: str | None = Field(default=None, max_length=160)
    token_count: int = Field(default=0, ge=0)
    data_labels: tuple[str, ...] = ()
    allowed_purposes: tuple[str, ...] = ()
    canonical: bool = False


class ContextReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["artifact", "evidence", "tool_call", "child_result", "path"]
    ref: str = Field(min_length=1, max_length=1_000)
    content_hash: str | None = Field(default=None, max_length=160)
    accessible: bool = True
    data_labels: tuple[str, ...] = ()
    allowed_purposes: tuple[str, ...] = ()


class ContinuationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    owner_type: ContextOwnerRole
    owner_id: str = Field(min_length=1, max_length=160)
    state_version: int = Field(ge=0)
    cancellation_epoch: int = Field(default=0, ge=0)
    window_number: int = Field(default=0, ge=0)
    source_item_ids: tuple[str, ...] = ()
    retained_tail_ids: tuple[str, ...] = ()
    action_idempotency_keys: tuple[str, ...] = ()
    waiting_state: dict[str, Any] = Field(default_factory=dict)
    remaining_budget: dict[str, int | float] = Field(default_factory=dict)
    contract_hash: str | None = Field(default=None, max_length=160)
    manifest_hash: str | None = Field(default=None, max_length=160)
    catalog_digests: dict[str, str] = Field(default_factory=dict)


class ContextEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    owner_type: ContextOwnerRole
    owner_id: str = Field(min_length=1, max_length=160)
    purpose: str = Field(min_length=1, max_length=4_000)
    protected_prefix: tuple[ContextItem, ...]
    prior_checkpoint: dict[str, Any] | None = None
    compactable_body: tuple[ContextItem, ...] = ()
    recent_tail: tuple[ContextItem, ...] = ()
    reference_manifest: tuple[ContextReference, ...] = ()
    accounting: TokenAccounting
    continuation: ContinuationManifest

    @model_validator(mode="after")
    def validate_owner_binding(self) -> ContextEnvelope:
        if (
            self.continuation.owner_type != self.owner_type
            or self.continuation.owner_id != self.owner_id
        ):
            raise ValueError("continuation manifest owner does not match envelope")
        return self


class CheckpointTrustMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    lossy: Literal[True] = True
    trusted_for_authorization: Literal[False] = False
    trusted_for_completion: Literal[False] = False
    untrusted_inputs: tuple[str, ...] = ()
    generated_from_canonical_state: bool = False


class VerifiedFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=4_000)
    evidence_refs: tuple[str, ...] = ()


class GlobalProgressItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    criterion_or_node: str = Field(min_length=1, max_length=1_000)
    status: str = Field(min_length=1, max_length=80)


class WorkspaceChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact_or_path_ref: str = Field(min_length=1, max_length=1_000)
    summary: str = Field(min_length=1, max_length=2_000)


class ChildResultReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    execution_id: str = Field(min_length=1, max_length=160)
    result_ref: str = Field(min_length=1, max_length=1_000)
    summary: str = Field(min_length=1, max_length=2_000)


class FailureDigest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fingerprint: str = Field(min_length=1, max_length=240)
    disposition: str = Field(min_length=1, max_length=1_000)


class RootContextCheckpointV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    checkpoint_role: Literal["root_execution"] = "root_execution"
    user_intent: str = Field(max_length=8_000)
    current_constraints: tuple[str, ...] = ()
    key_decisions: tuple[str, ...] = ()
    verified_facts: tuple[VerifiedFact, ...] = ()
    global_progress: tuple[GlobalProgressItem, ...] = ()
    workspace_changes: tuple[WorkspaceChange, ...] = ()
    child_results: tuple[ChildResultReference, ...] = ()
    recent_failures: tuple[FailureDigest, ...] = ()
    open_issues: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    trust: CheckpointTrustMetadata = Field(default_factory=CheckpointTrustMetadata)
    created_at: datetime


class ConversationContextCheckpointV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    checkpoint_role: Literal["conversation"] = "conversation"
    user_intent: str = Field(max_length=8_000)
    current_constraints: tuple[str, ...] = ()
    key_decisions: tuple[str, ...] = ()
    completed_outcomes: tuple[str, ...] = ()
    open_issues: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    trust: CheckpointTrustMetadata = Field(default_factory=CheckpointTrustMetadata)
    created_at: datetime


class ChildLocalFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=4_000)
    provenance_ref: str | None = Field(default=None, max_length=1_000)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ChildContextCheckpointV2(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[2] = 2
    checkpoint_role: Literal["child_execution"] = "child_execution"
    agent_execution_id: str = Field(min_length=1, max_length=160)
    manifest_hash: str = Field(min_length=1, max_length=160)
    contract_hash: str = Field(min_length=1, max_length=160)
    local_progress: tuple[str, ...] = ()
    completed_steps: tuple[str, ...] = ()
    local_facts: tuple[ChildLocalFact, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    recent_failures: tuple[FailureDigest, ...] = ()
    open_issues: tuple[str, ...] = ()
    next_action: str | None = Field(default=None, max_length=4_000)
    remaining_budget: dict[str, int | float] = Field(default_factory=dict)
    continuation_round_trips: int = Field(default=0, ge=0)
    continuation_answers: tuple[SubagentContinuationAnswer, ...] = ()
    trust: CheckpointTrustMetadata = Field(default_factory=CheckpointTrustMetadata)
    created_at: datetime


ChildCheckpoint = SubagentContextCheckpoint | ChildContextCheckpointV2
_CHILD_CHECKPOINT_ADAPTER = TypeAdapter(ChildCheckpoint)


def parse_child_checkpoint(value: dict[str, Any]) -> ChildCheckpoint:
    """Read V1 and V2 checkpoints without converting V1 facts to verified state."""
    return _CHILD_CHECKPOINT_ADAPTER.validate_python(value)


class CompactionMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    owner_type: ContextOwnerRole
    owner_id: str
    window_number: int = Field(ge=0)
    input_digest: str = Field(min_length=1, max_length=160)
    policy_version: str = Field(min_length=1, max_length=80)
    checkpoint_schema_version: int = Field(ge=1)
    implementation: CompactionImplementation
    status: CompactionLifecycleStatus
    state_version: int = Field(default=0, ge=0)
    cancellation_epoch: int = Field(default=0, ge=0)
    generation_provider: str | None = Field(default=None, max_length=120)
    generation_model: str | None = Field(default=None, max_length=240)
    token_before: int = Field(default=0, ge=0)
    token_after: int | None = Field(default=None, ge=0)
    source_item_ids: tuple[str, ...] = ()
    retained_tail_ids: tuple[str, ...] = ()
    failure_stage: str | None = Field(default=None, max_length=120)
    failure_code: str | None = Field(default=None, max_length=160)
