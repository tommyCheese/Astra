from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.grounding.schemas import Citation as GroundingCitation
from app.grounding.schemas import Claim as GroundingClaim
from app.schemas.models import RunModelConfig

SKILL_QUALIFIED_IDENTITY_RE = re.compile(
    r"^(?:builtin|custom):[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
)


class ReasoningEffort(str, Enum):
    fast = "fast"
    balanced = "balanced"
    deep = "deep"


TOOL_CALL_LIMIT_RANGES: dict[ReasoningEffort, tuple[int, int]] = {
    ReasoningEffort.fast: (0, 5),
    ReasoningEffort.balanced: (6, 15),
}

TOOL_CALL_LIMIT_DEFAULTS: dict[ReasoningEffort, int | None] = {
    ReasoningEffort.fast: 5,
    ReasoningEffort.balanced: 8,
    ReasoningEffort.deep: None,
}


def validate_tool_call_limit(effort: ReasoningEffort, value: int) -> int:
    if effort == ReasoningEffort.deep:
        raise ValueError("max_tool_calls must be unlimited for deep reasoning")
    minimum, maximum = TOOL_CALL_LIMIT_RANGES[effort]
    if not minimum <= value <= maximum:
        raise ValueError(
            f"max_tool_calls must be between {minimum} and {maximum} for {effort.value} reasoning"
        )
    return value


class PlanStatus(str, Enum):
    planned = "planned"
    active = "active"
    superseded = "superseded"
    completed = "completed"


class PlanNodeStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    blocked = "blocked"
    skipped = "skipped"


class NodeExecutionPhase(str, Enum):
    claimed = "claimed"
    running = "running"
    waiting_resource = "waiting_resource"
    waiting_approval = "waiting_approval"
    committing = "committing"
    cancelling = "cancelling"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    result_unknown = "result_unknown"


class NodeExecutionStatus(str, Enum):
    active = "active"
    waiting = "waiting"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    blocked = "blocked"


class ReflectionTrigger(str, Enum):
    failure_only = "failure_only"
    adaptive = "adaptive"
    every_turn = "every_turn"


class ExecutionMode(str, Enum):
    request_approval = "request_approval"
    auto_approval = "auto_approval"


class PlanExecution(str, Enum):
    auto = "auto"
    confirm = "confirm"


class ContinuationAction(str, Enum):
    execute_plan = "execute_plan"
    revise_plan = "revise_plan"


class VerificationLevel(str, Enum):
    basic = "basic"
    standard = "standard"
    strict = "strict"


class AnswerMode(str, Enum):
    standard = "standard"
    trusted = "trusted"


class AssuranceLevel(str, Enum):
    basic = "basic"
    full = "full"


class ContractMode(str, Enum):
    system_minimal = "system_minimal"
    model = "model"


class CriterionStatus(str, Enum):
    pending = "pending"
    satisfied = "satisfied"
    failed = "failed"
    waived = "waived"


class EvaluationOutcome(str, Enum):
    matched = "matched"
    partial = "partial"
    mismatch = "mismatch"
    conflict = "conflict"
    inconclusive = "inconclusive"


class TerminalState(str, Enum):
    continue_run = "continue"
    completed = "completed"
    completed_with_warnings = "completed_with_warnings"
    waiting_user = "waiting_user"
    blocked = "blocked"
    failed = "failed"


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
    model_routing: SubagentModelRoutingPolicy = Field(
        default_factory=SubagentModelRoutingPolicy
    )


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


class RunExecutionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_mode: AnswerMode
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
        return self


class SuccessCriterion(BaseModel):
    id: str
    description: str
    mandatory: bool = True
    verification_method: str
    status: CriterionStatus = CriterionStatus.pending
    evidence_refs: list[str] = Field(default_factory=list)
    provenance: dict[str, Any] = Field(default_factory=dict)


class TaskAssumption(BaseModel):
    id: str
    statement: str
    confidence: float = Field(default=0.5, ge=0, le=1)
    provenance: dict[str, Any] = Field(default_factory=dict)
    valid: bool = True


class VerificationRequirement(BaseModel):
    id: str
    validator: str
    mandatory: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class TaskContract(BaseModel):
    original_goal: str
    deliverables: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    prohibited_actions: list[str] = Field(default_factory=list)
    assumptions: list[TaskAssumption] = Field(default_factory=list)
    success_criteria: list[SuccessCriterion] = Field(default_factory=list)
    verification_requirements: list[VerificationRequirement] = Field(default_factory=list)
    skill_revisions: list[dict[str, str]] = Field(default_factory=list)
    risk_level: str = "low"
    ambiguity_status: str = "clear"
    clarification_question: str | None = None


class ExpectedObservation(BaseModel):
    kind: str
    success_condition: str
    required_fields: list[str] = Field(default_factory=list)


class PlanNodeDraft(BaseModel):
    node_key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    intent: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_skill_ids: list[str] = Field(default_factory=list)
    success_criteria_refs: list[str] = Field(default_factory=list)
    expected_outcome: ExpectedObservation
    risk_level: str = "low"
    optional: bool = False


class PlanDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[PlanNodeDraft] = Field(min_length=1)


class PlanNodeView(BaseModel):
    id: str
    plan_id: str
    plan_version: int
    node_key: str
    index: int
    title: str
    intent: str
    status: PlanNodeStatus
    depends_on: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    required_skill_ids: list[str] = Field(default_factory=list)
    success_criteria_refs: list[str] = Field(default_factory=list)
    expected_outcome: ExpectedObservation | None = None
    risk_level: str = "low"
    optional: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    failure: dict[str, Any] | None = None
    lineage_node_id: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class PlanEdgeView(BaseModel):
    id: str
    plan_id: str
    predecessor_node_id: str
    successor_node_id: str
    dependency_type: str = "hard"


class PlanView(BaseModel):
    schema_version: Literal[1, 2] = 2
    id: str
    run_id: str
    version: int
    status: PlanStatus
    supersedes_plan_id: str | None = None
    nodes: list[PlanNodeView] = Field(default_factory=list)
    edges: list[PlanEdgeView] = Field(default_factory=list)
    created_at: datetime | None = None
    activated_at: datetime | None = None
    completed_at: datetime | None = None
    active_executions: list[NodeExecutionView] = Field(default_factory=list)
    parallelism: ParallelismSummary | None = None


class ActiveExecutionSummary(BaseModel):
    execution_id: str | None = None
    plan_node_id: str
    plan_version: int = 0
    attempt: int = 1
    dispatch_batch_id: str | None = None
    slot_index: int | None = None
    phase: NodeExecutionPhase = NodeExecutionPhase.running
    status: NodeExecutionStatus = NodeExecutionStatus.active
    state_version: int = 1
    wait_reason: str | None = None
    started_at: datetime | None = None
    heartbeat_at: datetime | None = None


class ResourceLeaseView(BaseModel):
    id: str
    node_execution_id: str
    resource_summary: str
    mode: Literal["read", "write", "exclusive"]
    fencing_token: int
    acquired_at: datetime
    expires_at: datetime
    released_at: datetime | None = None
    release_reason: str | None = None


class BudgetReservationView(BaseModel):
    id: str
    node_execution_id: str
    budget_kind: str
    reserved: int
    consumed: int = 0
    status: str
    created_at: datetime
    settled_at: datetime | None = None


class NodeExecutionView(ActiveExecutionSummary):
    execution_id: str
    run_id: str
    plan_id: str
    worker_id: str | None = None
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    finished_at: datetime | None = None
    resource_leases: list[ResourceLeaseView] = Field(default_factory=list)
    budget_reservations: list[BudgetReservationView] = Field(default_factory=list)


class ParallelismSummary(BaseModel):
    requested_slots: int = Field(default=1, ge=1)
    total_slots: int = Field(default=1, ge=1)
    used_slots: int = Field(default=0, ge=0)
    active_count: int = Field(default=0, ge=0)
    waiting_count: int = Field(default=0, ge=0)


class PlanVersionSummary(BaseModel):
    id: str
    run_id: str
    version: int
    status: PlanStatus
    supersedes_plan_id: str | None = None
    node_count: int = 0
    created_at: datetime
    activated_at: datetime | None = None
    completed_at: datetime | None = None


class PlanNodeDiff(BaseModel):
    node_id: str
    node_key: str
    change: Literal["added", "removed", "unchanged", "modified", "inherited_completed"]
    previous_node_id: str | None = None


class PlanEdgeDiff(BaseModel):
    predecessor_node_id: str
    successor_node_id: str
    change: Literal["added", "removed", "unchanged"]


class PlanGraphDiff(BaseModel):
    from_plan_id: str
    to_plan_id: str
    from_version: int
    to_version: int
    nodes: list[PlanNodeDiff] = Field(default_factory=list)
    edges: list[PlanEdgeDiff] = Field(default_factory=list)


class PlanGraphSnapshotEvent(BaseModel):
    schema_version: Literal[1] = 1
    plan_id: str
    plan_version: int
    graph: PlanView


class PlanVersionEvent(BaseModel):
    schema_version: Literal[1] = 1
    plan_id: str
    plan_version: int
    supersedes_plan_id: str | None = None
    status: PlanStatus | None = None
    node_count: int | None = None
    lineage_count: int = 0


class PlanNodeTransitionEvent(BaseModel):
    schema_version: Literal[1] = 1
    plan_id: str
    plan_version: int
    plan_node_id: str
    node_key: str
    previous_status: PlanNodeStatus
    status: PlanNodeStatus
    evidence_refs: list[str] = Field(default_factory=list)
    failure: dict[str, Any] | None = None


class PlanRevisionEvent(BaseModel):
    schema_version: Literal[1] = 1
    plan_id: str
    plan_version: int
    state_version: int
    revised_plan_id: str | None = None
    revised_plan_version: int | None = None
    error_code: str | None = None


class PlanPatchOperation(BaseModel):
    operation: str
    node_key: str | None = None
    node: PlanNodeDraft | None = None
    predecessor_key: str | None = None
    successor_key: str | None = None
    updates: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class PlanPatch(BaseModel):
    expected_plan_version: int = Field(ge=1)
    reason: str
    operations: list[PlanPatchOperation] = Field(min_length=1)


class AcceptedFact(BaseModel):
    id: str
    statement: str
    provenance: dict[str, Any]
    confidence: float = Field(default=0.5, ge=0, le=1)
    conflicts_with: list[str] = Field(default_factory=list)


class FailureFingerprint(BaseModel):
    fingerprint: str
    tool_name: str | None = None
    error_category: str
    attempt_count: int = 1
    exhausted: bool = False


class AgentState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    version: int = 1
    task_contract: TaskContract
    policy_version: int = 1
    active_plan_id: str | None = None
    active_plan_version: int = 0
    active_executions: list[ActiveExecutionSummary] = Field(default_factory=list)
    accepted_facts: list[AcceptedFact] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    observations: list[dict[str, Any]] = Field(default_factory=list)
    evaluations: list[dict[str, Any]] = Field(default_factory=list)
    failures: list[FailureFingerprint] = Field(default_factory=list)
    budget_usage: dict[str, int] = Field(default_factory=dict)
    terminal_intent: str | None = None


class Evaluation(BaseModel):
    plan_node_id: str | None = None
    outcome: EvaluationOutcome
    summary: str
    expected: ExpectedObservation | None = None
    observation_refs: list[str] = Field(default_factory=list)
    criterion_updates: dict[str, CriterionStatus] = Field(default_factory=dict)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)


class ReflectionPatch(BaseModel):
    level: str
    revised_tool_input: dict[str, Any] | None = None
    invalidated_assumption_ids: list[str] = Field(default_factory=list)
    fact_updates: list[AcceptedFact] = Field(default_factory=list)
    criterion_updates: dict[str, CriterionStatus] = Field(default_factory=dict)
    plan_patch: PlanPatch | None = None
    added_verification_requirements: list[VerificationRequirement] = Field(default_factory=list)
    terminal_intent: str | None = None

    def actionable(self) -> bool:
        return any(
            (
                self.revised_tool_input,
                self.invalidated_assumption_ids,
                self.fact_updates,
                self.criterion_updates,
                self.plan_patch,
                self.added_verification_requirements,
                self.terminal_intent,
            )
        )


class CompletionDecision(BaseModel):
    state: TerminalState
    reason: str
    unmet_criteria: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_user_action: str | None = None


class NodeResult(BaseModel):
    next_node: str
    state_patch: dict[str, Any] = Field(default_factory=dict)
    events: list[dict[str, Any]] = Field(default_factory=list)
    error: dict[str, Any] | None = None


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


class ApprovalDecision(str, Enum):
    approve_once = "approve_once"
    allow_similar = "allow_similar"
    allow_task = "allow_task"
    reject = "reject"


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


class SourceReference(BaseModel):
    url: str
    title: str | None = None
    retrieved_at: str | None = None


class Finding(BaseModel):
    text: str
    source_urls: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    summary: str
    findings: list[Finding] = Field(default_factory=list)
    claims: list[GroundingClaim] = Field(default_factory=list)
    citations: list[GroundingCitation] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    failed_sources: list[dict[str, Any]] = Field(default_factory=list)
    source_quality: list[dict[str, Any]] = Field(default_factory=list)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    verification_notes: list[str] = Field(default_factory=list)
    memory_references: list[dict[str, Any]] = Field(default_factory=list)
    audit_refs: dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    decision_type: str
    reasoning_summary: str
    tool_name: str | None = None
    skill_identity: str | None = None
    skill_resource_path: str | None = None
    tool_input: dict[str, Any] = Field(default_factory=dict)
    expected_observation: str | None = None
    stop_condition: str | None = None
    target_step_id: str | None = None
    success_criteria_refs: list[str] = Field(default_factory=list)
    expected: ExpectedObservation | None = None
    risk_level: str = "low"
    confidence: float = Field(default=0.5, ge=0, le=1)
    fallback: str | None = None
    node_result: dict[str, Any] = Field(default_factory=dict)


class AgentObservation(BaseModel):
    plan_node_id: str | None = None
    kind: str
    status: str
    summary: str
    data: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class AgentReflection(BaseModel):
    trigger: str
    summary: str
    next_action: str
    retry: bool = False
    revised_tool_input: dict[str, Any] | None = None
    level: str = "local"
    diagnosis: str | None = None
    invalidated_assumptions: list[str] = Field(default_factory=list)
    violated_criteria: list[str] = Field(default_factory=list)
    patch: ReflectionPatch | None = None


class MemoryRecord(BaseModel):
    id: str | None = None
    memory_key: str | None = None
    namespace_type: str | None = None
    namespace_id: str | None = None
    scope: str
    kind: str
    status: str = "candidate"
    version: int = 1
    state_version: int = 1
    content: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    importance: float = 0.5
    utility_score: float = 0.0
    observed_at: datetime | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None
    supersedes_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None


class ValidationIssue(BaseModel):
    code: str
    message: str
    severity: str = "error"
    evidence_refs: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationOutcome(BaseModel):
    validator: str
    passed: bool
    blocking: bool = True
    requirement_ids: list[str] = Field(default_factory=list)
    issues: list[ValidationIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class VerificationReport(BaseModel):
    status: str
    assurance_level: AssuranceLevel = AssuranceLevel.full
    source_count: int = 0
    caveat_count: int = 0
    low_quality_sources: list[dict[str, Any]] = Field(default_factory=list)
    failed_sources: list[dict[str, Any]] = Field(default_factory=list)
    memory_references: list[dict[str, Any]] = Field(default_factory=list)
    invalid_artifact_references: int = 0
    notes: list[str] = Field(default_factory=list)
    validation_outcomes: list[ValidationOutcome] = Field(default_factory=list)


class FailedSource(BaseModel):
    url: str | None = None
    title: str | None = None
    type: str | None = None
    category: str | None = None
    code: str | None = None
    message: str | None = None
    retryable: bool = False
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class SourceQuality(BaseModel):
    url: str
    title: str | None = None
    quality_score: float | None = None
    extraction_strategy: str | None = None
    warnings: list[str] = Field(default_factory=list)


class ConflictRecord(BaseModel):
    statement: str | None = None
    conflicting_statement: str | None = None
    source_urls: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class ResultMemoryReference(BaseModel):
    id: str | None = None
    scope: str | None = None
    kind: str | None = None
    content: str | None = None
    confidence: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class AuditReferences(BaseModel):
    evidence_pack_artifact_id: str | None = None
    evidence_ledger_artifact_id: str | None = None
    evidence_record_count: int = 0
    agent_turn_count: int = 0
    referenced_artifact_ids: list[str] = Field(default_factory=list)


class RunError(BaseModel):
    type: str
    code: str
    message: str
    retryable: bool = False
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class RunResult(BaseModel):
    """Stable API boundary for persisted runner result JSON."""

    model_config = ConfigDict(extra="forbid")

    summary: str = ""
    answer_mode: AnswerMode = AnswerMode.trusted
    assurance_level: AssuranceLevel = AssuranceLevel.full
    findings: list[Finding] = Field(default_factory=list)
    claims: list[GroundingClaim] = Field(default_factory=list)
    citations: list[GroundingCitation] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    failed_sources: list[FailedSource] = Field(default_factory=list)
    source_quality: list[SourceQuality] = Field(default_factory=list)
    conflicts: list[ConflictRecord] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    verification_notes: list[str] = Field(default_factory=list)
    memory_references: list[ResultMemoryReference] = Field(default_factory=list)
    audit_refs: AuditReferences = Field(default_factory=AuditReferences)
    verification_report: VerificationReport | None = None
    completion_decision: CompletionDecision | None = None
    error: RunError | None = None


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


class MemoryView(BaseModel):
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
    answer_mode: AnswerMode = AnswerMode.trusted
    execution_profile: dict[str, Any] = Field(default_factory=dict)
    summary: str | None
    result: RunResult | None
    steps: list[StepView]
    tool_calls: list[ToolCallView]
    artifacts: list[ArtifactView]
    sandbox_jobs: list[SandboxJobView] = Field(default_factory=list)
    events: list[RunEventView]
    turns: list[AgentTurnView] = Field(default_factory=list)
    memories: list[MemoryView] = Field(default_factory=list)
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
