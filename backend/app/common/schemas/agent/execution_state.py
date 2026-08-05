from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.common.schemas.agent.planning import (
    ActiveExecutionSummary,
    ExpectedObservation,
    PlanPatch,
    TaskContract,
    VerificationRequirement,
)
from app.common.schemas.agent.types import CriterionStatus, EvaluationOutcome, TerminalState


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


class AgentObservationEvaluation(BaseModel):
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
