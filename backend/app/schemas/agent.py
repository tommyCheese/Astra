from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    goal: str = Field(min_length=1, max_length=4000)


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
