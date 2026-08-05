from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class TokenTotals(BaseModel):
    input: int = 0
    cached_input: int = 0
    output: int = 0
    reasoning: int = 0
    total: int = 0


class UsageOverview(BaseModel):
    model_invocations: int = 0
    successful_invocations: int = 0
    failed_invocations: int = 0
    interrupted_invocations: int = 0
    agent_turns: int = 0
    tool_calls: int = 0
    successful_tool_calls: int = 0
    failed_tool_calls: int = 0
    tool_success_rate: float | None = None
    memories: int = 0
    sandbox_jobs: int = 0
    artifacts: int = 0
    artifact_bytes: int = 0


class UsageTrendPoint(BaseModel):
    date: str
    invocations: int
    tokens: int
    tool_calls: int


class UsageModelBreakdown(BaseModel):
    provider: str
    model: str
    invocations: int
    reported_invocations: int
    tokens: TokenTotals


class UsageToolBreakdown(BaseModel):
    tool_name: str
    calls: int
    succeeded: int
    failed: int
    success_rate: float | None


class UsageCoverage(BaseModel):
    reported_invocations: int
    total_invocations: int
    ratio: float
    complete: bool


class UsageSummary(BaseModel):
    scope: Literal["all", "task", "run"]
    from_time: datetime | None = Field(default=None, alias="from")
    to_time: datetime | None = Field(default=None, alias="to")
    overview: UsageOverview
    tokens: TokenTotals
    coverage: UsageCoverage
    trend: list[UsageTrendPoint]
    models: list[UsageModelBreakdown]
    tools: list[UsageToolBreakdown]

    model_config = {"populate_by_name": True}
