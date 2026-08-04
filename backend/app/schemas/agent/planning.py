from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.agent.types import (
    CriterionStatus,
    NodeExecutionPhase,
    NodeExecutionStatus,
    PlanNodeStatus,
    PlanStatus,
)


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
