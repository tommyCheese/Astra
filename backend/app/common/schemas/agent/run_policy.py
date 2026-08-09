from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.common.schemas.agent.types import (
    AnswerMode,
    AssuranceLevel,
    ContractMode,
    ExecutionMode,
    PlanExecution,
    ReasoningEffort,
    ReflectionTrigger,
    RuntimeKind,
    VerificationLevel,
    validate_tool_call_limit,
)

EXECUTABLE_SUBAGENT_COHORTS = frozenset({"trusted_read_only"})


class RunBudgets(BaseModel):
    max_plan_depth: int = 6
    max_candidate_strategies: int = 2
    max_model_calls: int = 24
    max_reflections: int = 3
    max_replans: int = 2
    max_turns: int | None = 12
    max_tool_calls: int | None = 8
    verification_coverage: int = 2
    max_parallel_nodes: int = Field(default=3, ge=1)


class SubagentBudgetPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    max_children_total: int = Field(default=0, ge=0)
    max_children_per_parent: int = Field(default=0, ge=0)
    max_parallel_children: int = Field(default=0, ge=0)
    max_depth: int = Field(default=1, ge=1)
    max_parent_round_trips: int = Field(default=0, ge=0)
    max_wall_time_seconds: int = Field(default=300, ge=1)
    max_tokens: int = Field(default=0, ge=0)
    max_model_calls: int = Field(default=0, ge=0)
    max_tool_calls: int = Field(default=0, ge=0)
    max_cost_usd: float = Field(default=0, ge=0)
    parent_token_reserve: int = Field(default=0, ge=0)
    parent_model_call_reserve: int = Field(default=0, ge=0)
    parent_tool_call_reserve: int = Field(default=0, ge=0)
    parent_cost_reserve_usd: float = Field(default=0, ge=0)


class SubagentModelRoutingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed_providers: tuple[str, ...] = ()
    allowed_models: tuple[str, ...] = ()
    require_same_provider: bool = True
    allow_lower_cost_model: bool = True
    max_reasoning_effort: ReasoningEffort = ReasoningEffort.balanced


class EffectiveSubagentPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = False
    kill_switch: bool = False
    rollout_cohort: str = "disabled"
    read_only: bool = True
    allowed_join_policies: tuple[str, ...] = ("required", "optional")
    budgets: SubagentBudgetPolicy = Field(default_factory=SubagentBudgetPolicy)
    model_routing: SubagentModelRoutingPolicy = Field(default_factory=SubagentModelRoutingPolicy)


class RequestedReasoningPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_effort: ReasoningEffort = ReasoningEffort.balanced
    max_tool_calls: int | None = Field(default=None, ge=0)
    reflection_enabled: bool = True
    reflection_trigger: ReflectionTrigger = ReflectionTrigger.adaptive
    execution_mode: ExecutionMode = ExecutionMode.request_approval
    verification_level: VerificationLevel = VerificationLevel.standard

    @model_validator(mode="after")
    def validate_tool_budget(self) -> RequestedReasoningPolicy:
        if self.max_tool_calls is not None:
            validate_tool_call_limit(self.reasoning_effort, self.max_tool_calls)
        return self


class EffectiveReasoningPolicy(RequestedReasoningPolicy):
    budgets: RunBudgets = Field(default_factory=RunBudgets)
    subagents: EffectiveSubagentPolicy = Field(default_factory=EffectiveSubagentPolicy)


class PolicyAdjustment(BaseModel):
    field: str
    requested: Any
    effective: Any
    rule: str
    reason: str


class ReasoningPolicySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested: RequestedReasoningPolicy = Field(default_factory=RequestedReasoningPolicy)
    effective: EffectiveReasoningPolicy = Field(default_factory=EffectiveReasoningPolicy)
    adjustments: list[PolicyAdjustment] = Field(default_factory=list)
    version: Literal[2] = 2


class FastRuntimePolicy(BaseModel):
    """Small deployment policy for the independent model-driven runtime."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    protocol_version: Literal[1] = 1
    max_context_messages: int = Field(default=40, ge=1, le=400)
    max_consecutive_tool_actions: int = Field(default=12, ge=1, le=100)
    max_protocol_retries: int = Field(default=1, ge=0, le=3)
    memory_recall_enabled: bool = False


class FastPendingAction(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    action_id: str = Field(min_length=1, max_length=120)
    kind: Literal["model", "tool", "approval"]
    idempotency_key: str | None = Field(default=None, max_length=240)
    phase: Literal["decided", "prepared", "executing", "waiting"] = "decided"
    tool_name: str | None = Field(default=None, max_length=120)
    tool_input: dict[str, Any] = Field(default_factory=dict)
    idempotent: bool | None = None


class FastRuntimeSnapshot(BaseModel):
    """Durable state for Fast Runtime; deliberately excludes trusted lifecycle state."""

    model_config = ConfigDict(extra="forbid")

    protocol_version: Literal[1] = 1
    snapshot_version: int = Field(default=0, ge=0)
    turn_index: int = Field(default=0, ge=0)
    messages: list[dict[str, Any]] = Field(default_factory=list, max_length=400)
    recent_observations: list[dict[str, Any]] = Field(default_factory=list, max_length=100)
    pending_action: FastPendingAction | None = None
    terminal_intent: Literal["answer", "ask_user", "stop"] | None = None


class RunExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_mode: AnswerMode
    runtime_kind: RuntimeKind
    runtime_version: int = Field(default=1, ge=1)
    fast_runtime_policy: FastRuntimePolicy | None = None
    contract_mode: ContractMode
    assurance_level: AssuranceLevel
    reasoning_policy: ReasoningPolicySnapshot
    plan_execution: PlanExecution | None = None
    validators: list[str] = Field(default_factory=list)
    interactive: bool = True
    permission_bundle: dict[str, Any] | None = None
    subagent_mode: Literal["auto", "required"] = "auto"
    version: Literal[2] = 2

    @model_validator(mode="after")
    def validate_mode_shape(self) -> RunExecutionProfile:
        if self.answer_mode == AnswerMode.standard and self.plan_execution is not None:
            raise ValueError("plan_execution is only valid for trusted runs")
        if self.answer_mode == AnswerMode.trusted and self.plan_execution is None:
            raise ValueError("trusted runs require plan_execution")
        if self.answer_mode == AnswerMode.trusted and self.runtime_kind != RuntimeKind.trusted_v1:
            raise ValueError("trusted runs require trusted-v1 runtime")
        if self.answer_mode == AnswerMode.standard and self.runtime_kind != RuntimeKind.fast_v1:
            raise ValueError("standard runs require fast-v1 runtime")
        if self.runtime_kind == RuntimeKind.fast_v1 and self.fast_runtime_policy is None:
            raise ValueError("fast-v1 runs require fast_runtime_policy")
        if self.runtime_kind != RuntimeKind.fast_v1 and self.fast_runtime_policy is not None:
            raise ValueError("fast_runtime_policy is only valid for fast-v1 runs")
        return self
