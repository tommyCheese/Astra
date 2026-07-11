from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)
    task_id: Optional[str] = None


class CreateRunResponse(BaseModel):
    task_id: str
    run_id: str
    status: str


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
