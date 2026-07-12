from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ReasoningEffort(str, Enum):
    fast = "fast"
    balanced = "balanced"
    deep = "deep"


class PlanningStrategy(str, Enum):
    direct = "direct"
    adaptive = "adaptive"
    plan_first = "plan_first"


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
    planning_strategy: PlanningStrategy = PlanningStrategy.adaptive
    reflection_enabled: bool = True
    reflection_trigger: ReflectionTrigger = ReflectionTrigger.adaptive
    execution_mode: ExecutionMode = ExecutionMode.request_approval
    verification_level: VerificationLevel = VerificationLevel.standard


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
    adjustments: List[PolicyAdjustment] = Field(default_factory=list)
    version: int = 1


class SuccessCriterion(BaseModel):
    id: str
    description: str
    mandatory: bool = True
    verification_method: str
    status: CriterionStatus = CriterionStatus.pending
    evidence_refs: List[str] = Field(default_factory=list)


class TaskAssumption(BaseModel):
    id: str
    statement: str
    confidence: float = Field(default=0.5, ge=0, le=1)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    valid: bool = True


class VerificationRequirement(BaseModel):
    id: str
    validator: str
    mandatory: bool = True
    config: Dict[str, Any] = Field(default_factory=dict)


class TaskContract(BaseModel):
    original_goal: str
    deliverables: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    prohibited_actions: List[str] = Field(default_factory=list)
    assumptions: List[TaskAssumption] = Field(default_factory=list)
    success_criteria: List[SuccessCriterion] = Field(default_factory=list)
    verification_requirements: List[VerificationRequirement] = Field(default_factory=list)
    risk_level: str = "low"
    ambiguity_status: str = "clear"
    clarification_question: Optional[str] = None


class ExpectedObservation(BaseModel):
    kind: str
    success_condition: str
    required_fields: List[str] = Field(default_factory=list)


class PlanGraphStep(BaseModel):
    id: str
    title: str
    intent: str
    depends_on: List[str] = Field(default_factory=list)
    required_capabilities: List[str] = Field(default_factory=list)
    success_criteria_refs: List[str] = Field(default_factory=list)
    expected_outcome: Optional[ExpectedObservation] = None
    risk_level: str = "low"
    status: str = "pending"
    evidence_refs: List[str] = Field(default_factory=list)


class PlanGraph(BaseModel):
    version: int = 1
    strategy: PlanningStrategy = PlanningStrategy.adaptive
    steps: List[PlanGraphStep] = Field(default_factory=list)

    def ready_steps(self) -> List[PlanGraphStep]:
        completed = {step.id for step in self.steps if step.status == "completed"}
        return [step for step in self.steps if step.status == "pending" and set(step.depends_on) <= completed]


class AcceptedFact(BaseModel):
    id: str
    statement: str
    provenance: Dict[str, Any]
    confidence: float = Field(default=0.5, ge=0, le=1)
    conflicts_with: List[str] = Field(default_factory=list)


class FailureFingerprint(BaseModel):
    fingerprint: str
    tool_name: Optional[str] = None
    error_category: str
    attempt_count: int = 1
    exhausted: bool = False


class AgentState(BaseModel):
    version: int = 1
    task_contract: TaskContract
    policy_version: int = 1
    plan: PlanGraph
    accepted_facts: List[AcceptedFact] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    observations: List[Dict[str, Any]] = Field(default_factory=list)
    evaluations: List[Dict[str, Any]] = Field(default_factory=list)
    failures: List[FailureFingerprint] = Field(default_factory=list)
    budget_usage: Dict[str, int] = Field(default_factory=dict)
    terminal_intent: Optional[str] = None


class Evaluation(BaseModel):
    outcome: EvaluationOutcome
    summary: str
    expected: Optional[ExpectedObservation] = None
    observation_refs: List[str] = Field(default_factory=list)
    criterion_updates: Dict[str, CriterionStatus] = Field(default_factory=dict)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)


class ReflectionPatch(BaseModel):
    level: str
    revised_tool_input: Optional[Dict[str, Any]] = None
    invalidated_assumption_ids: List[str] = Field(default_factory=list)
    fact_updates: List[AcceptedFact] = Field(default_factory=list)
    criterion_updates: Dict[str, CriterionStatus] = Field(default_factory=dict)
    replacement_plan: Optional[PlanGraph] = None
    added_verification_requirements: List[VerificationRequirement] = Field(default_factory=list)
    terminal_intent: Optional[str] = None

    def actionable(self) -> bool:
        return any((self.revised_tool_input, self.invalidated_assumption_ids, self.fact_updates, self.criterion_updates, self.replacement_plan, self.added_verification_requirements, self.terminal_intent))


class CompletionDecision(BaseModel):
    state: TerminalState
    reason: str
    unmet_criteria: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    required_user_action: Optional[str] = None


class NodeResult(BaseModel):
    next_node: str
    state_patch: Dict[str, Any] = Field(default_factory=dict)
    events: List[Dict[str, Any]] = Field(default_factory=list)
    error: Optional[Dict[str, Any]] = None


class CreateRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    task_id: Optional[str] = None
    reasoning_policy: RequestedReasoningPolicy = Field(default_factory=RequestedReasoningPolicy)
    model: Optional[Dict[str, str]] = None


class CreateRunResponse(BaseModel):
    task_id: str
    run_id: str
    status: str


class ContinueRunRequest(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    approved: Optional[bool] = None
    continuation_token: Optional[str] = None


class PlanStep(BaseModel):
    title: str
    intent: str
    required_tools: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)


class PlanOutput(BaseModel):
    steps: List[PlanStep]
    required_tools: List[str] = Field(default_factory=list)
    success_criteria: List[str] = Field(default_factory=list)
    risk_level: str = "low"


class ToolDecision(BaseModel):
    tool_name: str
    input: Dict[str, Any]
    reason: str


class SourceReference(BaseModel):
    url: str
    title: Optional[str] = None
    retrieved_at: Optional[str] = None


class Finding(BaseModel):
    text: str
    source_urls: List[str] = Field(default_factory=list)


class FinalAnswer(BaseModel):
    summary: str
    findings: List[Finding] = Field(default_factory=list)
    sources: List[SourceReference] = Field(default_factory=list)
    failed_sources: List[Dict[str, Any]] = Field(default_factory=list)
    source_quality: List[Dict[str, Any]] = Field(default_factory=list)
    conflicts: List[Dict[str, Any]] = Field(default_factory=list)
    caveats: List[str] = Field(default_factory=list)
    verification_notes: List[str] = Field(default_factory=list)
    memory_references: List[Dict[str, Any]] = Field(default_factory=list)
    audit_refs: Dict[str, Any] = Field(default_factory=dict)


class AgentDecision(BaseModel):
    decision_type: str
    reasoning_summary: str
    tool_name: Optional[str] = None
    tool_input: Dict[str, Any] = Field(default_factory=dict)
    expected_observation: Optional[str] = None
    stop_condition: Optional[str] = None
    target_step_id: Optional[str] = None
    success_criteria_refs: List[str] = Field(default_factory=list)
    expected: Optional[ExpectedObservation] = None
    risk_level: str = "low"
    confidence: float = Field(default=0.5, ge=0, le=1)
    fallback: Optional[str] = None


class AgentObservation(BaseModel):
    kind: str
    status: str
    summary: str
    data: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[Dict[str, Any]] = None


class AgentReflection(BaseModel):
    trigger: str
    summary: str
    next_action: str
    retry: bool = False
    revised_tool_input: Optional[Dict[str, Any]] = None
    level: str = "local"
    diagnosis: Optional[str] = None
    invalidated_assumptions: List[str] = Field(default_factory=list)
    violated_criteria: List[str] = Field(default_factory=list)
    patch: Optional[ReflectionPatch] = None


class MemoryRecord(BaseModel):
    id: Optional[str] = None
    scope: str
    kind: str
    content: str
    structured_data: Dict[str, Any] = Field(default_factory=dict)
    provenance: Dict[str, Any] = Field(default_factory=dict)
    confidence: float = 0.5
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class AgentTurn(BaseModel):
    id: Optional[str] = None
    run_id: Optional[str] = None
    turn_index: int
    decision_type: str
    reasoning_summary: str
    selected_tool: Optional[str] = None
    decision: Dict[str, Any] = Field(default_factory=dict)
    observation: Optional[Dict[str, Any]] = None
    reflection: Optional[Dict[str, Any]] = None
    tool_call_id: Optional[str] = None
    artifact_id: Optional[str] = None
    memory_reads: List[Dict[str, Any]] = Field(default_factory=list)
    memory_writes: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = "created"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class VerificationReport(BaseModel):
    status: str
    source_count: int = 0
    caveat_count: int = 0
    low_quality_sources: List[Dict[str, Any]] = Field(default_factory=list)
    failed_sources: List[Dict[str, Any]] = Field(default_factory=list)
    memory_references: List[Dict[str, Any]] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)


class CandidateSource(BaseModel):
    url: str
    title: str
    snippet: str
    provider: str = "mock"
    rank: Optional[int] = None
    display_link: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    retrieved_at: str


class CrawlerPlan(BaseModel):
    strategy: str = "readability"
    selectors: List[str] = Field(default_factory=list)
    exclude_selectors: List[str] = Field(default_factory=list)
    target: str = "main_content"


class ExtractedSource(BaseModel):
    url: str
    status_code: int
    title: Optional[str] = None
    description: Optional[str] = None
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    extraction_strategy: str
    quality_score: float
    content_length: int
    source_type: str = "web_page"
    warnings: List[str] = Field(default_factory=list)
    retrieved_at: str


class FetchOutput(BaseModel):
    url: str
    status_code: int
    title: Optional[str] = None
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    retrieved_at: str


class EvidencePack(BaseModel):
    query: str
    candidates: List[CandidateSource] = Field(default_factory=list)
    fetched_sources: List[ExtractedSource] = Field(default_factory=list)
    failed_sources: List[Dict[str, Any]] = Field(default_factory=list)
    dedupe: Dict[str, Any] = Field(default_factory=dict)
    warnings: List[str] = Field(default_factory=list)


class StepView(BaseModel):
    id: str
    index: int
    title: str
    intent: str
    status: str
    evidence: Optional[Dict[str, Any]] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ToolCallView(BaseModel):
    id: str
    step_id: Optional[str]
    tool_name: str
    tool_version: str
    input: Dict[str, Any]
    output: Optional[Dict[str, Any]]
    status: str
    permission: str
    side_effect_level: str
    started_at: datetime
    completed_at: Optional[datetime]
    error: Optional[Dict[str, Any]]


class ArtifactView(BaseModel):
    id: str
    type: str
    path: Optional[str]
    content_ref: Optional[str]
    metadata: Dict[str, Any]
    created_at: datetime


class RunEventView(BaseModel):
    id: int
    type: str
    payload: Dict[str, Any]
    created_at: datetime


class AgentTurnView(BaseModel):
    id: str
    run_id: str
    turn_index: int
    decision_type: str
    reasoning_summary: str
    selected_tool: Optional[str] = None
    decision: Dict[str, Any]
    observation: Optional[Dict[str, Any]]
    reflection: Optional[Dict[str, Any]]
    tool_call_id: Optional[str]
    artifact_id: Optional[str]
    memory_reads: List[Dict[str, Any]]
    memory_writes: List[Dict[str, Any]]
    status: str
    evaluation: Optional[Dict[str, Any]] = None
    reflection_patch: Optional[Dict[str, Any]] = None
    state_version_before: Optional[int] = None
    state_version_after: Optional[int] = None
    plan_version: int = 1
    phase: str = "created"
    idempotency_key: Optional[str] = None
    paused_node: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MemoryView(BaseModel):
    id: str
    run_id: Optional[str]
    scope: str
    kind: str
    content: str
    structured_data: Dict[str, Any]
    provenance: Dict[str, Any]
    confidence: float
    created_at: datetime
    updated_at: datetime
    expires_at: Optional[datetime]


class ChatMessageView(BaseModel):
    id: str
    role: str
    content: str
    status: str = "completed"
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RunView(BaseModel):
    id: str
    task_id: str
    status: str
    mode: str
    summary: Optional[str]
    result: Optional[Dict[str, Any]]
    steps: List[StepView]
    tool_calls: List[ToolCallView]
    artifacts: List[ArtifactView]
    events: List[RunEventView]
    turns: List[AgentTurnView] = Field(default_factory=list)
    memories: List[MemoryView] = Field(default_factory=list)
    chat_messages: List[ChatMessageView] = Field(default_factory=list)
    verification_report: Optional[VerificationReport] = None
    reasoning_policy: Dict[str, Any] = Field(default_factory=dict)
    task_contract: Dict[str, Any] = Field(default_factory=dict)
    plan_graph: Dict[str, Any] = Field(default_factory=dict)
    agent_state: Dict[str, Any] = Field(default_factory=dict)
    state_version: int = 0
    terminal_reason: Optional[Dict[str, Any]] = None
    waiting_state: Optional[Dict[str, Any]] = None
    task_adapter: str = "legacy_web"
