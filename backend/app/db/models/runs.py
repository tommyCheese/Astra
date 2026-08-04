from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models.conversations import TaskRecord
    from app.db.models.executions import (
        AgentExecutionRecord,
        AgentJoinRecord,
        BudgetReservationRecord,
        ModelInvocationRecord,
        NodeExecutionRecord,
        ResourceLeaseRecord,
    )
    from app.db.models.memory import MemoryRecord
    from app.db.models.permissions import (
        AgentIdentityRecord,
        ApprovalGrantRecord,
        ApprovalRequestRecord,
        DataFlowStateRecord,
        ToolCallRecord,
        ToolCatalogSnapshotRecord,
    )
    from app.db.models.plans import PlanRecord
    from app.db.models.skills import RunSkillSnapshotRecord
    from app.db.models.workspaces import (
        ArtifactRecord,
        SandboxJobRecord,
    )

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.model_base import Base, JsonType, utc_now, uuid_str


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_task_status", "task_id", "status"),
        Index("ix_runs_memory_session_id", "memory_session_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    memory_session_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="created")
    mode: Mapped[str] = mapped_column(String(80), default="web_data_query")
    answer_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="standard")
    execution_profile: Mapped[dict] = mapped_column(JsonType, default=dict)
    model_policy: Mapped[dict] = mapped_column(JsonType, default=dict)
    agent_profile_snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    active_plan_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    reasoning_policy: Mapped[dict] = mapped_column(JsonType, default=dict)
    task_contract: Mapped[dict] = mapped_column(JsonType, default=dict)
    plan_graph: Mapped[dict] = mapped_column(JsonType, default=dict)
    agent_state: Mapped[dict] = mapped_column(JsonType, default=dict)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    cancellation_epoch: Mapped[int] = mapped_column(Integer, default=0)
    terminal_reason: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    waiting_state: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    task_adapter: Mapped[str] = mapped_column(String(80), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    task: Mapped[TaskRecord] = relationship(back_populates="runs")
    steps: Mapped[list[StepRecord]] = relationship(
        back_populates="run", order_by="StepRecord.index"
    )
    tool_calls: Mapped[list[ToolCallRecord]] = relationship(back_populates="run")
    artifacts: Mapped[list[ArtifactRecord]] = relationship(back_populates="run")
    events: Mapped[list[RunEventRecord]] = relationship(
        back_populates="run", order_by="RunEventRecord.id"
    )
    turns: Mapped[list[AgentTurnRecord]] = relationship(
        back_populates="run", order_by="AgentTurnRecord.turn_index"
    )
    memories: Mapped[list[MemoryRecord]] = relationship(back_populates="run")
    sandbox_jobs: Mapped[list[SandboxJobRecord]] = relationship(back_populates="run")
    model_invocations: Mapped[list[ModelInvocationRecord]] = relationship(back_populates="run")
    approval_requests: Mapped[list[ApprovalRequestRecord]] = relationship(
        back_populates="run", order_by="ApprovalRequestRecord.created_at"
    )
    approval_grants: Mapped[list[ApprovalGrantRecord]] = relationship(back_populates="run")
    agent_identities: Mapped[list[AgentIdentityRecord]] = relationship(back_populates="run")
    tool_catalog_snapshot: Mapped[ToolCatalogSnapshotRecord | None] = relationship(
        back_populates="run", uselist=False
    )
    data_flow_state: Mapped[DataFlowStateRecord | None] = relationship(
        back_populates="run", uselist=False
    )
    plans: Mapped[list[PlanRecord]] = relationship(
        back_populates="run", foreign_keys="PlanRecord.run_id", order_by="PlanRecord.version"
    )
    node_executions: Mapped[list[NodeExecutionRecord]] = relationship(
        back_populates="run", order_by="NodeExecutionRecord.started_at"
    )
    resource_leases: Mapped[list[ResourceLeaseRecord]] = relationship(back_populates="run")
    budget_reservations: Mapped[list[BudgetReservationRecord]] = relationship(back_populates="run")
    skill_snapshot: Mapped[RunSkillSnapshotRecord | None] = relationship(
        back_populates="run", uselist=False
    )
    evidence_records: Mapped[list[EvidenceRecord]] = relationship(
        back_populates="run",
        order_by="EvidenceRecord.created_at",
        cascade="all, delete-orphan",
    )
    agent_executions: Mapped[list[AgentExecutionRecord]] = relationship(
        back_populates="run",
        order_by="AgentExecutionRecord.created_at",
        cascade="all, delete-orphan",
    )
    agent_joins: Mapped[list[AgentJoinRecord]] = relationship(
        back_populates="run",
        order_by="AgentJoinRecord.created_at",
        cascade="all, delete-orphan",
    )


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"
    __table_args__ = (
        UniqueConstraint("run_id", "evidence_key", name="uq_evidence_records_run_key"),
        Index("ix_evidence_records_run_kind", "run_id", "kind"),
        Index("ix_evidence_records_tool_call", "tool_call_id"),
        Index("ix_evidence_records_agent_execution", "agent_execution_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_executions.id"), nullable=True
    )
    evidence_id: Mapped[str] = mapped_column(String(40))
    evidence_key: Mapped[str] = mapped_column(String(320))
    kind: Mapped[str] = mapped_column(String(40))
    payload_digest: Mapped[str] = mapped_column(String(64))
    fragment: Mapped[dict] = mapped_column(JsonType)
    plan_node_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    node_execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="evidence_records")


class StepRecord(Base):
    __tablename__ = "steps"
    __table_args__ = (Index("ix_steps_run_id_index", "run_id", "index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240))
    intent: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    depends_on: Mapped[list] = mapped_column(JsonType, default=list)
    evidence: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="steps")
    tool_calls: Mapped[list[ToolCallRecord]] = relationship(back_populates="step")


class RunEventRecord(Base):
    __tablename__ = "run_events"
    __table_args__ = (
        Index("ix_run_events_run_id_id", "run_id", "id"),
        Index("ix_run_events_agent_execution_id", "agent_execution_id", "id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_executions.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="events")


class AgentTurnRecord(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (Index("ix_agent_turns_run_id_index", "run_id", "turn_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_executions.id"), nullable=True
    )
    plan_node_id: Mapped[str | None] = mapped_column(ForeignKey("plan_nodes.id"), nullable=True)
    node_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("node_executions.id"), nullable=True
    )
    turn_index: Mapped[int] = mapped_column(Integer)
    decision_type: Mapped[str] = mapped_column(String(40))
    reasoning_summary: Mapped[str] = mapped_column(Text)
    selected_tool: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision: Mapped[dict] = mapped_column(JsonType, default=dict)
    observation: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    reflection: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    memory_reads: Mapped[list] = mapped_column(JsonType, default=list)
    memory_writes: Mapped[list] = mapped_column(JsonType, default=list)
    status: Mapped[str] = mapped_column(String(40), default="created")
    evaluation: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    reflection_patch: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    state_version_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state_version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    phase: Mapped[str] = mapped_column(String(40), default="created")
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    paused_node: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="turns")
    node_execution: Mapped[NodeExecutionRecord | None] = relationship(back_populates="turns")
