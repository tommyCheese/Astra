from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ReasoningEffort(str, Enum):
    fast = "fast"
    balanced = "balanced"
    deep = "deep"


TOOL_CALL_LIMIT_RANGES: dict[ReasoningEffort, tuple[int, int]] = {
    ReasoningEffort.fast: (0, 5),
    ReasoningEffort.balanced: (6, 15),
    ReasoningEffort.deep: (16, 50),
}

TOOL_CALL_LIMIT_DEFAULTS: dict[ReasoningEffort, int] = {
    ReasoningEffort.fast: 5,
    ReasoningEffort.balanced: 8,
    ReasoningEffort.deep: 16,
}


def validate_tool_call_limit(effort: ReasoningEffort, value: int) -> int:
    minimum, maximum = TOOL_CALL_LIMIT_RANGES[effort]
    if not minimum <= value <= maximum:
        raise ValueError(
            f"max_tool_calls must be between {minimum} and {maximum} for {effort.value} reasoning"
        )
    return value


class PlanningStrategy(str, Enum):
    direct = "direct"
    adaptive = "adaptive"
    plan_first = "plan_first"


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


class ReflectionTrigger(str, Enum):
    failure_only = "failure_only"
    adaptive = "adaptive"
    every_turn = "every_turn"


class ExecutionMode(str, Enum):
    plan_only = "plan_only"
    request_approval = "request_approval"
    auto_approval = "auto_approval"


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


class RunBudgets(BaseModel):
    max_plan_depth: int = 6
    max_candidate_strategies: int = 2
    max_model_calls: int = 24
    max_reflections: int = 3
    max_replans: int = 2
    max_turns: int = 12
    max_tool_calls: int = 8
    verification_coverage: int = 2


class RequestedReasoningPolicy(BaseModel):
    reasoning_effort: ReasoningEffort = ReasoningEffort.balanced
    max_tool_calls: int | None = Field(default=None, ge=0, le=50)
    planning_strategy: PlanningStrategy = PlanningStrategy.adaptive
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


class PolicyAdjustment(BaseModel):
    field: str
    requested: Any
    effective: Any
    rule: str
    reason: str


class ReasoningPolicySnapshot(BaseModel):
    requested: RequestedReasoningPolicy = Field(default_factory=RequestedReasoningPolicy)
    effective: EffectiveReasoningPolicy = Field(default_factory=EffectiveReasoningPolicy)
    adjustments: list[PolicyAdjustment] = Field(default_factory=list)
    version: int = 1


class RunExecutionProfile(BaseModel):
    answer_mode: AnswerMode
    contract_mode: ContractMode
    assurance_level: AssuranceLevel
    reasoning_policy: ReasoningPolicySnapshot
    validators: list[str] = Field(default_factory=list)
    version: int = 1


class SuccessCriterion(BaseModel):
    id: str
    description: str
    mandatory: bool = True
    verification_method: str
    status: CriterionStatus = CriterionStatus.pending
    evidence_refs: list[str] = Field(default_factory=list)


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
    risk_level: str = "low"
    ambiguity_status: str = "clear"
    clarification_question: str | None = None


class ExpectedObservation(BaseModel):
    kind: str
    success_condition: str
    required_fields: list[str] = Field(default_factory=list)


class PlanGraphStep(BaseModel):
    id: str
    title: str
    intent: str
    depends_on: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    success_criteria_refs: list[str] = Field(default_factory=list)
    expected_outcome: ExpectedObservation | None = None
    risk_level: str = "low"
    status: str = "pending"
    evidence_refs: list[str] = Field(default_factory=list)


class PlanGraph(BaseModel):
    version: int = 1
    strategy: PlanningStrategy = PlanningStrategy.adaptive
    steps: list[PlanGraphStep] = Field(default_factory=list)

    def ready_steps(self) -> list[PlanGraphStep]:
        completed = {step.id for step in self.steps if step.status == "completed"}
        return [
            step
            for step in self.steps
            if step.status == "pending" and set(step.depends_on) <= completed
        ]


class PlanNodeDraft(BaseModel):
    node_key: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    intent: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    success_criteria_refs: list[str] = Field(default_factory=list)
    expected_outcome: ExpectedObservation
    risk_level: str = "low"
    optional: bool = False


class PlanDraft(BaseModel):
    strategy: PlanningStrategy = PlanningStrategy.adaptive
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
    success_criteria_refs: list[str] = Field(default_factory=list)
    expected_outcome: ExpectedObservation | None = None
    risk_level: str = "low"
    optional: bool = False
    evidence_refs: list[str] = Field(default_factory=list)
    failure: dict[str, Any] | None = None


class PlanView(BaseModel):
    id: str
    run_id: str
    version: int
    strategy: PlanningStrategy
    status: PlanStatus
    supersedes_plan_id: str | None = None
    nodes: list[PlanNodeView] = Field(default_factory=list)


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
    version: int = 1
    task_contract: TaskContract
    policy_version: int = 1
    active_plan_id: str | None = None
    active_plan_version: int = 0
    active_node_id: str | None = None
    # Read-only compatibility for Runs created before canonical Plan persistence.
    plan: PlanGraph | None = None
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
    replacement_plan: PlanGraph | None = None
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
                self.replacement_plan,
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
    goal: str = Field(min_length=1, max_length=4000)
    task_id: str | None = None
    answer_mode: AnswerMode = AnswerMode.standard
    reasoning_policy: RequestedReasoningPolicy = Field(default_factory=RequestedReasoningPolicy)
    model: dict[str, str] | None = None


class CreateRunResponse(BaseModel):
    task_id: str
    run_id: str
    status: str
    answer_mode: AnswerMode


class ContinueRunRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    approved: bool | None = None
    continuation_token: str | None = None
    model: dict[str, str] | None = None


class PlanStep(BaseModel):
    title: str
    intent: str
    required_tools: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)


class PlanOutput(BaseModel):
    steps: list[PlanStep]
    required_tools: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    risk_level: str = "low"


class ToolDecision(BaseModel):
    tool_name: str
    input: dict[str, Any]
    reason: str


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
    scope: str
    kind: str
    content: str
    structured_data: dict[str, Any] = Field(default_factory=dict)
    provenance: dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    created_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None


class AgentTurn(BaseModel):
    id: str | None = None
    run_id: str | None = None
    turn_index: int
    decision_type: str
    reasoning_summary: str
    selected_tool: str | None = None
    decision: dict[str, Any] = Field(default_factory=dict)
    observation: dict[str, Any] | None = None
    reflection: dict[str, Any] | None = None
    tool_call_id: str | None = None
    artifact_id: str | None = None
    memory_reads: list[dict[str, Any]] = Field(default_factory=list)
    memory_writes: list[dict[str, Any]] = Field(default_factory=list)
    status: str = "created"
    created_at: datetime | None = None
    updated_at: datetime | None = None


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

    model_config = ConfigDict(extra="ignore")

    summary: str = ""
    answer_mode: AnswerMode = AnswerMode.trusted
    assurance_level: AssuranceLevel = AssuranceLevel.full
    findings: list[Finding] = Field(default_factory=list)
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

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_result(cls, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {"summary": str(value) if value is not None else ""}

        normalized = dict(value)
        summary = normalized.get("summary")
        normalized["summary"] = summary if isinstance(summary, str) else str(summary or "")
        normalized["findings"] = _validated_records(
            normalized.get("findings"), Finding, scalar_field="text"
        )
        normalized["sources"] = _validated_records(
            normalized.get("sources"), SourceReference, scalar_field="url"
        )
        normalized["failed_sources"] = _validated_records(
            normalized.get("failed_sources"), FailedSource
        )
        normalized["source_quality"] = _validated_records(
            normalized.get("source_quality"), SourceQuality
        )
        normalized["conflicts"] = _validated_records(normalized.get("conflicts"), ConflictRecord)
        normalized["memory_references"] = _validated_records(
            normalized.get("memory_references"), ResultMemoryReference
        )
        for field_name in ("caveats", "verification_notes"):
            items = _as_list(normalized.get(field_name))
            normalized[field_name] = [str(item) for item in items if item is not None]
        normalized["audit_refs"] = _validated_value(
            normalized.get("audit_refs"), AuditReferences, default=AuditReferences()
        )
        normalized["verification_report"] = _validated_value(
            normalized.get("verification_report"), VerificationReport
        )
        normalized["completion_decision"] = _validated_value(
            normalized.get("completion_decision"), CompletionDecision
        )
        normalized["error"] = _validated_value(normalized.get("error"), RunError)
        return normalized


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _validated_records(
    value: Any,
    model: type[BaseModel],
    *,
    scalar_field: str | None = None,
) -> list[BaseModel]:
    records: list[BaseModel] = []
    for item in _as_list(value):
        if item is None:
            continue
        candidate = (
            {scalar_field: str(item)} if scalar_field and not isinstance(item, dict) else item
        )
        if not isinstance(candidate, dict):
            continue
        try:
            records.append(model.model_validate(candidate))
        except (TypeError, ValueError):
            continue
    return records


def _validated_value(
    value: Any,
    model: type[BaseModel],
    *,
    default: BaseModel | None = None,
) -> BaseModel | None:
    if not isinstance(value, dict):
        return default
    try:
        return model.model_validate(value)
    except (TypeError, ValueError):
        return default


class CandidateSource(BaseModel):
    url: str
    title: str
    snippet: str
    provider: str = "mock"
    rank: int | None = None
    display_link: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: str


class CrawlerPlan(BaseModel):
    strategy: str = "trafilatura"
    selectors: list[str] = Field(default_factory=list)
    exclude_selectors: list[str] = Field(default_factory=list)
    target: str = "main_content"


class ExtractedSource(BaseModel):
    url: str
    status_code: int
    title: str | None = None
    description: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    extraction_strategy: str
    quality_score: float
    content_length: int
    source_type: str = "web_page"
    warnings: list[str] = Field(default_factory=list)
    retrieved_at: str


class FetchOutput(BaseModel):
    url: str
    status_code: int
    title: str | None = None
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    retrieved_at: str


class EvidencePack(BaseModel):
    query: str
    candidates: list[CandidateSource] = Field(default_factory=list)
    fetched_sources: list[ExtractedSource] = Field(default_factory=list)
    failed_sources: list[dict[str, Any]] = Field(default_factory=list)
    dedupe: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


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
    type: str
    payload: dict[str, Any]
    created_at: datetime


class AgentTurnView(BaseModel):
    id: str
    run_id: str
    plan_node_id: str | None = None
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
    scope: str
    kind: str
    content: str
    structured_data: dict[str, Any]
    provenance: dict[str, Any]
    confidence: float
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None


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
    reasoning_policy: dict[str, Any] = Field(default_factory=dict)
    task_contract: dict[str, Any] = Field(default_factory=dict)
    plan_graph: dict[str, Any] = Field(default_factory=dict)
    agent_state: dict[str, Any] = Field(default_factory=dict)
    state_version: int = 0
    terminal_reason: dict[str, Any] | None = None
    waiting_state: dict[str, Any] | None = None
    task_adapter: str = "web"
    agent_profile: dict[str, Any] = Field(default_factory=dict)
