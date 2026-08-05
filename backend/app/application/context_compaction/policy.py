from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.common.core.config import AstraRuntimeSettings
from app.common.schemas.context_compaction import (
    CompactionContextItem,
    ContextOwnerRole,
    ContextThresholdScope,
    ContextTokenAccounting,
)


class CapacityExit(str, Enum):
    context_capacity_error = "context_capacity_error"
    budget_limited = "budget_limited"


class CompactionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = "astra-context-compaction-v2"
    role: ContextOwnerRole
    threshold_scope: ContextThresholdScope
    trigger_ratio: float = Field(gt=0, lt=1)
    recovery_ratio: float = Field(gt=0, lt=1)
    recent_tail_tokens: int = Field(ge=0)
    recent_tail_max_ratio: float = Field(ge=0, le=0.5)
    protected_sections: tuple[str, ...]
    checkpoint_schema: str
    recent_tail_priority: tuple[str, ...]
    capacity_exit: CapacityExit
    enabled: bool
    shadow_mode: bool
    deterministic_emergency: bool
    max_attempts: int = Field(ge=1, le=4)


class RecentTailSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[CompactionContextItem, ...]
    token_count: int = Field(ge=0)
    first_retained_id: str | None = None


class CompactionTriggerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    should_compact: bool
    hard_cap_exceeded: bool
    measured_tokens: int = Field(ge=0)
    soft_threshold_tokens: int = Field(ge=0)
    recovery_target_tokens: int = Field(ge=0)
    reasons: tuple[str, ...] = ()


class ShadowCompactionProjection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: CompactionTriggerDecision
    projected_tokens_after: int = Field(ge=0)
    projected_reduction: int = Field(ge=0)
    would_install: Literal[False] = False


def build_compaction_policy(settings: AstraRuntimeSettings, role: ContextOwnerRole) -> CompactionPolicy:
    child = role == ContextOwnerRole.child_execution
    enabled_by_role = {
        ContextOwnerRole.conversation: settings.context_compaction_conversation_enabled,
        ContextOwnerRole.root_execution: settings.context_compaction_root_enabled,
        ContextOwnerRole.child_execution: settings.context_compaction_child_enabled,
    }
    protected = {
        ContextOwnerRole.conversation: ("platform", "current_request", "model_identity"),
        ContextOwnerRole.root_execution: (
            "current_request",
            "authorization",
            "skills",
            "canonical_runtime_state",
            "budget",
            "completion_gate",
        ),
        ContextOwnerRole.child_execution: (
            "delegation_contract",
            "role_protocol",
            "attenuated_authority",
            "catalog_digests",
            "workspace_scope",
            "local_plan",
            "budget",
            "termination",
        ),
    }
    return CompactionPolicy(
        role=role,
        threshold_scope=ContextThresholdScope(settings.context_compaction_threshold_scope),
        trigger_ratio=settings.context_auto_compact_ratio,
        recovery_ratio=settings.context_compaction_recovery_ratio,
        recent_tail_tokens=(
            settings.context_compaction_child_recent_tail_tokens
            if child
            else settings.context_compaction_recent_tail_tokens
        ),
        recent_tail_max_ratio=settings.context_compaction_recent_tail_max_ratio,
        protected_sections=protected[role],
        checkpoint_schema=(
            "ChildContextCheckpointV2"
            if child
            else "ConversationContextCheckpointV2"
            if role == ContextOwnerRole.conversation
            else "RootContextCheckpointV2"
        ),
        recent_tail_priority=("user_correction", "tool_error", "observation", "model_input"),
        capacity_exit=(
            CapacityExit.budget_limited if child else CapacityExit.context_capacity_error
        ),
        enabled=settings.context_compaction_v2_enabled and enabled_by_role[role],
        shadow_mode=settings.context_compaction_shadow_mode,
        deterministic_emergency=settings.context_compaction_deterministic_emergency_enabled,
        max_attempts=settings.context_compaction_max_attempts,
    )


def recent_tail_budget(policy: CompactionPolicy, accounting: ContextTokenAccounting) -> int:
    ratio_budget = int(accounting.usable_input * policy.recent_tail_max_ratio)
    return max(0, min(policy.recent_tail_tokens, ratio_budget))


def select_recent_tail(items: tuple[CompactionContextItem, ...], token_budget: int) -> RecentTailSelection:
    selected: list[CompactionContextItem] = []
    used = 0
    for item in reversed(items):
        size = max(0, item.token_count)
        if selected and used + size > token_budget:
            continue
        if not selected and size > token_budget:
            continue
        selected.append(item)
        used += size
    selected.reverse()
    return RecentTailSelection(
        items=tuple(selected),
        token_count=used,
        first_retained_id=selected[0].id if selected else None,
    )


def evaluate_compaction_trigger(
    accounting: ContextTokenAccounting,
    policy: CompactionPolicy,
    *,
    provider_changed: bool = False,
    model_changed: bool = False,
    previous_context_window: int | None = None,
) -> CompactionTriggerDecision:
    if policy.threshold_scope == ContextThresholdScope.total:
        measured = accounting.total_tokens
    else:
        measured = max(
            0,
            accounting.total_tokens
            - accounting.protected_prefix_tokens
            - accounting.checkpoint_tokens
            - accounting.prefill_tokens,
        )
    soft = int(accounting.usable_input * policy.trigger_ratio)
    recovery = int(accounting.usable_input * policy.recovery_ratio)
    reasons: list[str] = []
    if measured >= soft:
        reasons.append("soft_threshold")
    if accounting.total_tokens >= accounting.usable_input:
        reasons.append("usable_input_exhausted")
    hard = accounting.total_tokens > accounting.context_window - accounting.output_reserve
    if hard:
        reasons.append("hard_cap")
    if provider_changed:
        reasons.append("provider_switch")
    if model_changed:
        reasons.append("model_switch")
    if previous_context_window is not None and accounting.context_window < previous_context_window:
        reasons.append("model_downshift")
    switch_requires_compaction = bool(
        {"provider_switch", "model_switch", "model_downshift"}.intersection(reasons)
        and accounting.total_tokens >= soft
    )
    return CompactionTriggerDecision(
        should_compact=hard or measured >= soft or switch_requires_compaction,
        hard_cap_exceeded=hard,
        measured_tokens=measured,
        soft_threshold_tokens=soft,
        recovery_target_tokens=recovery,
        reasons=tuple(reasons),
    )


def project_shadow_compaction(
    accounting: ContextTokenAccounting,
    policy: CompactionPolicy,
    *,
    expected_checkpoint_tokens: int,
) -> ShadowCompactionProjection:
    decision = evaluate_compaction_trigger(accounting, policy)
    tail = recent_tail_budget(policy, accounting)
    projected = min(
        accounting.total_tokens,
        accounting.protected_prefix_tokens + expected_checkpoint_tokens + tail,
    )
    return ShadowCompactionProjection(
        decision=decision,
        projected_tokens_after=projected,
        projected_reduction=max(0, accounting.total_tokens - projected),
    )
