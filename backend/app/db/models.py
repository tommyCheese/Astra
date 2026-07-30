import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def uuid_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


JsonType = JSON().with_variant(JSONB, "postgresql")


class ToolSettingRecord(Base):
    __tablename__ = "tool_settings"

    tool_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConversationStrategyPreferenceRecord(Base):
    __tablename__ = "conversation_strategy_preferences"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default="default")
    preferred_answer_mode: Mapped[str] = mapped_column(
        String(40), nullable=False, default="standard"
    )
    reasoning_effort: Mapped[str] = mapped_column(String(40), nullable=False)
    max_tool_calls: Mapped[int | None] = mapped_column(Integer, nullable=True, default=8)
    reflection_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reflection_trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TaskRecord(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_retention_scan", "pinned_at", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="created")
    priority: Mapped[str | None] = mapped_column(String(40), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(40), nullable=True)
    preferred_answer_mode: Mapped[str] = mapped_column(
        String(40), nullable=False, default="standard"
    )
    title_source: Mapped[str] = mapped_column(String(20), default="auto")
    context_state: Mapped[dict] = mapped_column(JsonType, default=dict)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    runs: Mapped[list["RunRecord"]] = relationship(back_populates="task")
    share: Mapped["ConversationShareRecord | None"] = relationship(
        back_populates="conversation", uselist=False
    )
    task_workspace: Mapped["TaskWorkspaceRecord | None"] = relationship(
        back_populates="task", uselist=False
    )


class ConversationShareRecord(Base):
    __tablename__ = "conversation_shares"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_conversation_shares_conversation_id"),
        UniqueConstraint("token", name="uq_conversation_shares_token"),
        Index("ix_conversation_shares_conversation_id", "conversation_id"),
        Index("ix_conversation_shares_token", "token"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    token: Mapped[str] = mapped_column(String(120))
    snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped[TaskRecord] = relationship(back_populates="share")


class RunRecord(Base):
    __tablename__ = "runs"
    __table_args__ = (
        Index("ix_runs_task_status", "task_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
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
    terminal_reason: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    waiting_state: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    task_adapter: Mapped[str] = mapped_column(String(80), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    task: Mapped[TaskRecord] = relationship(back_populates="runs")
    steps: Mapped[list["StepRecord"]] = relationship(
        back_populates="run", order_by="StepRecord.index"
    )
    tool_calls: Mapped[list["ToolCallRecord"]] = relationship(back_populates="run")
    artifacts: Mapped[list["ArtifactRecord"]] = relationship(back_populates="run")
    events: Mapped[list["RunEventRecord"]] = relationship(
        back_populates="run", order_by="RunEventRecord.id"
    )
    turns: Mapped[list["AgentTurnRecord"]] = relationship(
        back_populates="run", order_by="AgentTurnRecord.turn_index"
    )
    memories: Mapped[list["MemoryRecord"]] = relationship(back_populates="run")
    sandbox_jobs: Mapped[list["SandboxJobRecord"]] = relationship(back_populates="run")
    model_invocations: Mapped[list["ModelInvocationRecord"]] = relationship(back_populates="run")
    approval_requests: Mapped[list["ApprovalRequestRecord"]] = relationship(
        back_populates="run", order_by="ApprovalRequestRecord.created_at"
    )
    approval_grants: Mapped[list["ApprovalGrantRecord"]] = relationship(back_populates="run")
    agent_identities: Mapped[list["AgentIdentityRecord"]] = relationship(back_populates="run")
    tool_catalog_snapshot: Mapped["ToolCatalogSnapshotRecord | None"] = relationship(
        back_populates="run", uselist=False
    )
    data_flow_state: Mapped["DataFlowStateRecord | None"] = relationship(
        back_populates="run", uselist=False
    )
    plans: Mapped[list["PlanRecord"]] = relationship(
        back_populates="run", foreign_keys="PlanRecord.run_id", order_by="PlanRecord.version"
    )
    node_executions: Mapped[list["NodeExecutionRecord"]] = relationship(
        back_populates="run", order_by="NodeExecutionRecord.started_at"
    )
    resource_leases: Mapped[list["ResourceLeaseRecord"]] = relationship(back_populates="run")
    budget_reservations: Mapped[list["BudgetReservationRecord"]] = relationship(
        back_populates="run"
    )
    skill_snapshot: Mapped["RunSkillSnapshotRecord | None"] = relationship(
        back_populates="run", uselist=False
    )
    evidence_records: Mapped[list["EvidenceRecord"]] = relationship(
        back_populates="run",
        order_by="EvidenceRecord.created_at",
        cascade="all, delete-orphan",
    )


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"
    __table_args__ = (
        UniqueConstraint("run_id", "evidence_key", name="uq_evidence_records_run_key"),
        Index("ix_evidence_records_run_kind", "run_id", "kind"),
        Index("ix_evidence_records_tool_call", "tool_call_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
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


class SkillBlobRecord(Base):
    __tablename__ = "skill_blobs"

    digest: Mapped[str] = mapped_column(String(80), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    size_bytes: Mapped[int] = mapped_column(Integer)
    media_type: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SkillAuditRecord(Base):
    __tablename__ = "skill_audit_events"
    __table_args__ = (Index("ix_skill_audit_skill_created", "skill_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    skill_id: Mapped[str | None] = mapped_column(
        ForeignKey("skills.id"), nullable=True
    )
    type: Mapped[str] = mapped_column(String(120))
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SkillRecord(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("name", name="uq_skills_name"),
        Index("ix_skills_origin_enabled", "origin", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(64))
    origin: Mapped[str] = mapped_column(String(20))
    description: Mapped[str] = mapped_column(String(1024), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    active_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    draft: Mapped["SkillDraftRecord | None"] = relationship(
        back_populates="skill", uselist=False, cascade="all, delete-orphan"
    )
    revisions: Mapped[list["SkillRevisionRecord"]] = relationship(
        back_populates="skill",
        cascade="all, delete-orphan",
        order_by="SkillRevisionRecord.version",
    )


class SkillDraftRecord(Base):
    __tablename__ = "skill_drafts"

    skill_id: Mapped[str] = mapped_column(
        ForeignKey("skills.id"), primary_key=True
    )
    revision_token: Mapped[str] = mapped_column(String(36), default=uuid_str)
    files: Mapped[dict] = mapped_column(JsonType, default=dict)
    validation_report: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    skill: Mapped[SkillRecord] = relationship(back_populates="draft")


class SkillRevisionRecord(Base):
    __tablename__ = "skill_revisions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_revisions_version"),
        Index("ix_skill_revisions_skill_published", "skill_id", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"))
    version: Mapped[int] = mapped_column(Integer)
    digest: Mapped[str] = mapped_column(String(80))
    frontmatter: Mapped[dict] = mapped_column(JsonType, default=dict)
    manifest: Mapped[dict] = mapped_column(JsonType, default=dict)
    validation_report: Mapped[dict] = mapped_column(JsonType, default=dict)
    predecessor_id: Mapped[str | None] = mapped_column(
        ForeignKey("skill_revisions.id"), nullable=True
    )
    test_only: Mapped[bool] = mapped_column(Boolean, default=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    skill: Mapped[SkillRecord] = relationship(back_populates="revisions")


class RunSkillSnapshotRecord(Base):
    __tablename__ = "run_skill_snapshots"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_run_skill_snapshots_run"),
        Index("ix_run_skill_snapshots_catalog_digest", "catalog_digest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    catalog_digest: Mapped[str] = mapped_column(String(80))
    catalog: Mapped[list] = mapped_column(JsonType, default=list)
    activations: Mapped[list] = mapped_column(JsonType, default=list)
    resource_reads: Mapped[list] = mapped_column(JsonType, default=list)
    answer_mode: Mapped[str] = mapped_column(String(40))
    draft_test: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="skill_snapshot")


class PlanRecord(Base):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("run_id", "version", name="uq_plans_run_version"),
        Index("ix_plans_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="planned")
    supersedes_plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="plans", foreign_keys=[run_id])
    nodes: Mapped[list["PlanNodeRecord"]] = relationship(
        back_populates="plan", order_by="PlanNodeRecord.index", cascade="all, delete-orphan"
    )
    edges: Mapped[list["PlanEdgeRecord"]] = relationship(
        back_populates="plan", cascade="all, delete-orphan"
    )
    executions: Mapped[list["NodeExecutionRecord"]] = relationship(back_populates="plan")


class PlanNodeRecord(Base):
    __tablename__ = "plan_nodes"
    __table_args__ = (
        UniqueConstraint("plan_id", "node_key", name="uq_plan_nodes_plan_key"),
        UniqueConstraint("plan_id", "index", name="uq_plan_nodes_plan_index"),
        Index("ix_plan_nodes_plan_status", "plan_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    node_key: Mapped[str] = mapped_column(String(120))
    index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240))
    intent: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    required_capabilities: Mapped[list] = mapped_column(JsonType, default=list)
    required_skill_ids: Mapped[list] = mapped_column(JsonType, default=list)
    success_criteria_refs: Mapped[list] = mapped_column(JsonType, default=list)
    expected_outcome: Mapped[dict] = mapped_column(JsonType, default=dict)
    risk_level: Mapped[str] = mapped_column(String(40), default="low")
    optional: Mapped[bool] = mapped_column(Boolean, default=False)
    evidence_refs: Mapped[list] = mapped_column(JsonType, default=list)
    failure: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    lineage_node_id: Mapped[str | None] = mapped_column(ForeignKey("plan_nodes.id"), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    plan: Mapped[PlanRecord] = relationship(back_populates="nodes", foreign_keys=[plan_id])
    outgoing_edges: Mapped[list["PlanEdgeRecord"]] = relationship(
        back_populates="predecessor", foreign_keys="PlanEdgeRecord.predecessor_id"
    )
    incoming_edges: Mapped[list["PlanEdgeRecord"]] = relationship(
        back_populates="successor", foreign_keys="PlanEdgeRecord.successor_id"
    )
    tool_calls: Mapped[list["ToolCallRecord"]] = relationship(back_populates="plan_node")
    executions: Mapped[list["NodeExecutionRecord"]] = relationship(back_populates="plan_node")


class PlanEdgeRecord(Base):
    __tablename__ = "plan_edges"
    __table_args__ = (
        UniqueConstraint("plan_id", "predecessor_id", "successor_id", name="uq_plan_edges_nodes"),
        Index("ix_plan_edges_successor", "successor_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    predecessor_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id"))
    successor_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id"))
    dependency_type: Mapped[str] = mapped_column(String(40), default="hard")

    plan: Mapped[PlanRecord] = relationship(back_populates="edges")
    predecessor: Mapped[PlanNodeRecord] = relationship(
        back_populates="outgoing_edges", foreign_keys=[predecessor_id]
    )
    successor: Mapped[PlanNodeRecord] = relationship(
        back_populates="incoming_edges", foreign_keys=[successor_id]
    )


class NodeExecutionRecord(Base):
    __tablename__ = "node_executions"
    __table_args__ = (
        UniqueConstraint(
            "plan_node_id",
            "attempt",
            name="uq_node_executions_node_attempt",
        ),
        UniqueConstraint(
            "plan_node_id",
            "current_slot",
            name="uq_node_executions_current_slot",
        ),
        UniqueConstraint(
            "run_id",
            "slot_index",
            name="uq_node_executions_run_slot",
        ),
        Index("ix_node_executions_run_status", "run_id", "status"),
        Index("ix_node_executions_plan_status", "plan_id", "status"),
        Index("ix_node_executions_heartbeat", "status", "heartbeat_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    plan_version: Mapped[int] = mapped_column(Integer)
    plan_node_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id"))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    dispatch_batch_id: Mapped[str] = mapped_column(String(36))
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phase: Mapped[str] = mapped_column(String(40), default="claimed")
    status: Mapped[str] = mapped_column(String(40), default="active")
    current_slot: Mapped[str | None] = mapped_column(
        String(16), nullable=True, default="current"
    )
    slot_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    wait_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)
    checkpoint: Mapped[dict] = mapped_column(JsonType, default=dict)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    failure: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="node_executions")
    plan: Mapped[PlanRecord] = relationship(back_populates="executions")
    plan_node: Mapped[PlanNodeRecord] = relationship(back_populates="executions")
    tool_calls: Mapped[list["ToolCallRecord"]] = relationship(
        back_populates="node_execution"
    )
    turns: Mapped[list["AgentTurnRecord"]] = relationship(back_populates="node_execution")
    approval_requests: Mapped[list["ApprovalRequestRecord"]] = relationship(
        back_populates="node_execution"
    )
    resource_leases: Mapped[list["ResourceLeaseRecord"]] = relationship(
        back_populates="node_execution", cascade="all, delete-orphan"
    )
    budget_reservations: Mapped[list["BudgetReservationRecord"]] = relationship(
        back_populates="node_execution", cascade="all, delete-orphan"
    )


class ResourceLeaseRecord(Base):
    __tablename__ = "resource_leases"
    __table_args__ = (
        UniqueConstraint(
            "node_execution_id",
            "resource_key",
            "mode",
            name="uq_resource_leases_execution_resource_mode",
        ),
        Index("ix_resource_leases_run_active", "run_id", "released_at", "expires_at"),
        Index("ix_resource_leases_resource_active", "resource_key", "released_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    node_execution_id: Mapped[str] = mapped_column(ForeignKey("node_executions.id"))
    resource_key: Mapped[str] = mapped_column(String(240))
    resource_summary: Mapped[str] = mapped_column(String(160))
    mode: Mapped[str] = mapped_column(String(20))
    fencing_token: Mapped[int] = mapped_column(Integer)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    release_reason: Mapped[str | None] = mapped_column(String(80), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="resource_leases")
    node_execution: Mapped[NodeExecutionRecord] = relationship(
        back_populates="resource_leases"
    )


class BudgetReservationRecord(Base):
    __tablename__ = "budget_reservations"
    __table_args__ = (
        UniqueConstraint(
            "node_execution_id",
            "budget_kind",
            name="uq_budget_reservations_execution_kind",
        ),
        Index("ix_budget_reservations_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    node_execution_id: Mapped[str] = mapped_column(ForeignKey("node_executions.id"))
    budget_kind: Mapped[str] = mapped_column(String(40))
    reserved: Mapped[int] = mapped_column(Integer)
    consumed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="reserved")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="budget_reservations")
    node_execution: Mapped[NodeExecutionRecord] = relationship(
        back_populates="budget_reservations"
    )


class ModelInvocationRecord(Base):
    __tablename__ = "model_invocations"
    __table_args__ = (
        Index("ix_model_invocations_run_created", "run_id", "created_at"),
        Index("ix_model_invocations_provider_model", "provider", "model"),
        Index("ix_model_invocations_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(160))
    operation: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="running")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    raw_usage: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="model_invocations")


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
    tool_calls: Mapped[list["ToolCallRecord"]] = relationship(back_populates="step")


class ToolCallRecord(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_run_id", "run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    step_id: Mapped[str | None] = mapped_column(ForeignKey("steps.id"), nullable=True)
    plan_node_id: Mapped[str | None] = mapped_column(ForeignKey("plan_nodes.id"), nullable=True)
    node_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("node_executions.id"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(120))
    tool_version: Mapped[str] = mapped_column(String(40))
    input: Mapped[dict] = mapped_column(JsonType)
    output: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    permission: Mapped[str] = mapped_column(String(80))
    side_effect_level: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[dict | None] = mapped_column(JsonType, nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="tool_calls")
    step: Mapped[StepRecord | None] = relationship(back_populates="tool_calls")
    plan_node: Mapped[PlanNodeRecord | None] = relationship(back_populates="tool_calls")
    node_execution: Mapped[NodeExecutionRecord | None] = relationship(
        back_populates="tool_calls"
    )
    approval_request: Mapped["ApprovalRequestRecord | None"] = relationship(
        back_populates="tool_call", uselist=False
    )


class ApprovalRequestRecord(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_requests_run_status", "run_id", "status"),
        UniqueConstraint("tool_call_id", name="uq_approval_requests_tool_call_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    turn_id: Mapped[str] = mapped_column(ForeignKey("agent_turns.id"))
    tool_call_id: Mapped[str] = mapped_column(ForeignKey("tool_calls.id"))
    node_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("node_executions.id"), nullable=True
    )
    execution_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_execution_state_version: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(120))
    tool_version: Mapped[str] = mapped_column(String(40))
    frozen_input: Mapped[dict] = mapped_column(JsonType)
    input_hash: Mapped[str] = mapped_column(String(64))
    frozen_effect_plan: Mapped[dict] = mapped_column(JsonType, default=dict)
    effect_plan_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    analyzer_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    analyzer_digest: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewer_identity: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    preview: Mapped[str] = mapped_column(Text)
    permission: Mapped[str] = mapped_column(String(80))
    impact: Mapped[str] = mapped_column(String(80))
    similar_matcher: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    decision: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="approval_requests")
    tool_call: Mapped[ToolCallRecord] = relationship(back_populates="approval_request")
    node_execution: Mapped[NodeExecutionRecord | None] = relationship(
        back_populates="approval_requests"
    )


class ApprovalGrantRecord(Base):
    __tablename__ = "approval_grants"
    __table_args__ = (
        Index("ix_approval_grants_run_tool", "run_id", "tool_name"),
        Index("ix_approval_grants_task_scope", "task_id", "scope", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    scope: Mapped[str] = mapped_column(String(40), default="run")
    subject: Mapped[dict] = mapped_column(JsonType, default=dict)
    tool_name: Mapped[str] = mapped_column(String(120))
    tool_version: Mapped[str] = mapped_column(String(40))
    matcher: Mapped[dict] = mapped_column(JsonType)
    effect_kinds: Mapped[list] = mapped_column(JsonType, default=list)
    resource_matcher: Mapped[dict] = mapped_column(JsonType, default=dict)
    invocation_constraints: Mapped[dict] = mapped_column(JsonType, default=dict)
    source_approval_id: Mapped[str] = mapped_column(ForeignKey("approval_requests.id"))
    status: Mapped[str] = mapped_column(String(40), default="active")
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    use_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="approval_grants")


class TaskWorkspaceRecord(Base):
    __tablename__ = "task_workspaces"
    __table_args__ = (UniqueConstraint("task_id", name="uq_task_workspaces_task_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    storage_key: Mapped[str] = mapped_column(String(500))
    status: Mapped[str] = mapped_column(String(40), default="active")
    quotas: Mapped[dict] = mapped_column(JsonType, default=dict)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    task: Mapped[TaskRecord] = relationship(back_populates="task_workspace")
    files: Mapped[list["WorkspaceFileRecord"]] = relationship(back_populates="workspace")
    changes: Mapped[list["WorkspaceChangeRecord"]] = relationship(back_populates="workspace")
    checkpoints: Mapped[list["WorkspaceCheckpointRecord"]] = relationship(
        back_populates="workspace"
    )


class WorkspaceFileRecord(Base):
    __tablename__ = "workspace_files"
    __table_args__ = (
        UniqueConstraint("workspace_id", "relative_path", name="uq_workspace_files_path"),
        Index("ix_workspace_files_workspace_status", "workspace_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("task_workspaces.id"))
    relative_path: Mapped[str] = mapped_column(String(1000))
    status: Mapped[str] = mapped_column(String(40), default="present")
    mime_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(120), nullable=True)
    security_status: Mapped[str] = mapped_column(String(40), default="pending")
    deliverable_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    workspace: Mapped[TaskWorkspaceRecord] = relationship(back_populates="files")


class WorkspaceCheckpointRecord(Base):
    __tablename__ = "workspace_checkpoints"
    __table_args__ = (
        Index("ix_workspace_checkpoints_workspace_created", "workspace_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("task_workspaces.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    manifest: Mapped[dict] = mapped_column(JsonType, default=dict)
    manifest_hash: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), default="valid")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    workspace: Mapped[TaskWorkspaceRecord] = relationship(back_populates="checkpoints")


class WorkspaceChangeRecord(Base):
    __tablename__ = "workspace_changes"
    __table_args__ = (
        Index("ix_workspace_changes_run_created", "run_id", "created_at"),
        Index("ix_workspace_changes_workspace_path", "workspace_id", "relative_path"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("task_workspaces.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    tool_call_id: Mapped[str | None] = mapped_column(ForeignKey("tool_calls.id"), nullable=True)
    checkpoint_id: Mapped[str | None] = mapped_column(
        ForeignKey("workspace_checkpoints.id"), nullable=True
    )
    relative_path: Mapped[str] = mapped_column(String(1000))
    change_kind: Mapped[str] = mapped_column(String(40))
    before_checksum: Mapped[str | None] = mapped_column(String(120), nullable=True)
    after_checksum: Mapped[str | None] = mapped_column(String(120), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    security_status: Mapped[str] = mapped_column(String(40), default="pending")
    deliverable_candidate: Mapped[bool] = mapped_column(Boolean, default=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    workspace: Mapped[TaskWorkspaceRecord] = relationship(back_populates="changes")


class AgentIdentityRecord(Base):
    __tablename__ = "agent_identities"
    __table_args__ = (
        Index("ix_agent_identities_run_type", "run_id", "identity_type"),
        Index("ix_agent_identities_task_type", "task_id", "identity_type"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    parent_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_identities.id"), nullable=True
    )
    identity_type: Mapped[str] = mapped_column(String(80))
    principal: Mapped[str] = mapped_column(String(240))
    trust_level: Mapped[str] = mapped_column(String(40), default="internal")
    attributes: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord | None] = relationship(back_populates="agent_identities")


class AgentDelegationRecord(Base):
    __tablename__ = "agent_delegations"
    __table_args__ = (
        Index("ix_agent_delegations_parent", "parent_identity_id", "revoked_at"),
        UniqueConstraint(
            "parent_identity_id",
            "child_identity_id",
            name="uq_agent_delegations_parent_child",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    parent_identity_id: Mapped[str] = mapped_column(ForeignKey("agent_identities.id"))
    child_identity_id: Mapped[str] = mapped_column(ForeignKey("agent_identities.id"))
    delegated_scope: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ToolCatalogSnapshotRecord(Base):
    __tablename__ = "tool_catalog_snapshots"
    __table_args__ = (UniqueConstraint("run_id", name="uq_tool_catalog_snapshots_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    catalog: Mapped[list] = mapped_column(JsonType, default=list)
    digest: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="tool_catalog_snapshot")


class CredentialGrantRecord(Base):
    __tablename__ = "credential_grants"
    __table_args__ = (
        Index("ix_credential_grants_run_service", "run_id", "service"),
        Index("ix_credential_grants_task_revoked", "task_id", "revoked_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_identity_id: Mapped[str] = mapped_column(ForeignKey("agent_identities.id"))
    service: Mapped[str] = mapped_column(String(160))
    tenant: Mapped[str | None] = mapped_column(String(240), nullable=True)
    scopes: Mapped[list] = mapped_column(JsonType, default=list)
    resources: Mapped[list] = mapped_column(JsonType, default=list)
    actions: Mapped[list] = mapped_column(JsonType, default=list)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DataFlowStateRecord(Base):
    __tablename__ = "data_flow_states"
    __table_args__ = (UniqueConstraint("run_id", name="uq_data_flow_states_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    trust_sources: Mapped[list] = mapped_column(JsonType, default=list)
    data_labels: Mapped[list] = mapped_column(JsonType, default=list)
    allowed_destinations: Mapped[list] = mapped_column(JsonType, default=list)
    prohibited_destinations: Mapped[list] = mapped_column(JsonType, default=list)
    retention: Mapped[dict] = mapped_column(JsonType, default=dict)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="data_flow_state")


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    type: Mapped[str] = mapped_column(String(80))
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(ForeignKey("tool_calls.id"), nullable=True)
    plan_node_id: Mapped[str | None] = mapped_column(ForeignKey("plan_nodes.id"), nullable=True)
    sandbox_job_id: Mapped[str | None] = mapped_column(ForeignKey("sandbox_jobs.id"), nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[str | None] = mapped_column(String(80), nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    preview_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    security_status: Mapped[str] = mapped_column(String(40), default="pending")
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="artifacts")


class SandboxJobRecord(Base):
    __tablename__ = "sandbox_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    tool_call_id: Mapped[str | None] = mapped_column(ForeignKey("tool_calls.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    runtime_profile: Mapped[dict] = mapped_column(JsonType, default=dict)
    resource_limits: Mapped[dict] = mapped_column(JsonType, default=dict)
    input_artifact_ids: Mapped[list] = mapped_column(JsonType, default=list)
    output_artifact_ids: Mapped[list] = mapped_column(JsonType, default=list)
    executor: Mapped[str] = mapped_column(String(80))
    runtime_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    image_digest: Mapped[str | None] = mapped_column(String(240), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    stdout_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    stderr_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="sandbox_jobs")


class RunEventRecord(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="events")


class AgentTurnRecord(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (Index("ix_agent_turns_run_id_index", "run_id", "turn_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
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
    node_execution: Mapped[NodeExecutionRecord | None] = relationship(
        back_populates="turns"
    )


class MemoryRecord(Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint(
            "namespace_type",
            "namespace_id",
            "memory_key",
            "version",
            name="uq_memories_namespace_key_version",
        ),
        Index("ix_memories_scope_kind", "scope", "kind"),
        Index(
            "ix_memories_namespace_status_kind",
            "namespace_type",
            "namespace_id",
            "status",
            "kind",
        ),
        Index("ix_memories_key_version", "memory_key", "version"),
        Index("ix_memories_status_expiry", "status", "expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    memory_key: Mapped[str] = mapped_column(String(240), default=uuid_str)
    namespace_type: Mapped[str] = mapped_column(String(40), default="run")
    namespace_id: Mapped[str] = mapped_column(String(120), default=uuid_str)
    scope: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="active")
    version: Mapped[int] = mapped_column(Integer, default=1)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[str] = mapped_column(Text)
    structured_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    confidence: Mapped[float] = mapped_column(Float)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    utility_score: Mapped[float] = mapped_column(Float, default=0.0)
    access_count: Mapped[int] = mapped_column(Integer, default=0)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("memories.id"), nullable=True
    )
    consolidation_generation: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[RunRecord | None] = relationship(back_populates="memories")
    sources: Mapped[list["MemorySourceRecord"]] = relationship(
        back_populates="memory",
        cascade="all, delete-orphan",
        foreign_keys="MemorySourceRecord.memory_id",
    )
    outgoing_links: Mapped[list["MemoryLinkRecord"]] = relationship(
        foreign_keys="MemoryLinkRecord.source_memory_id",
        cascade="all, delete-orphan",
    )
    incoming_links: Mapped[list["MemoryLinkRecord"]] = relationship(
        foreign_keys="MemoryLinkRecord.target_memory_id",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["MemoryAuditRecord"]] = relationship(
        back_populates="memory",
        cascade="all, delete-orphan",
    )


class MemorySourceRecord(Base):
    __tablename__ = "memory_sources"
    __table_args__ = (
        UniqueConstraint(
            "memory_id",
            "source_kind",
            "source_ref",
            name="uq_memory_sources_memory_kind_ref",
        ),
        Index("ix_memory_sources_run", "run_id"),
        Index("ix_memory_sources_memory_accessible", "memory_id", "accessible"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    source_kind: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str] = mapped_column(String(320))
    source_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("agent_turns.id"), nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(
        ForeignKey("tool_calls.id"), nullable=True
    )
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"), nullable=True)
    source_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    accessible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memory: Mapped[MemoryRecord] = relationship(
        back_populates="sources", foreign_keys=[memory_id]
    )


class MemoryLinkRecord(Base):
    __tablename__ = "memory_links"
    __table_args__ = (
        UniqueConstraint(
            "source_memory_id",
            "target_memory_id",
            "relation",
            name="uq_memory_links_source_target_relation",
        ),
        Index("ix_memory_links_target_relation", "target_memory_id", "relation"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    source_memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    target_memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    relation: Mapped[str] = mapped_column(String(40))
    link_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryRecallEventRecord(Base):
    __tablename__ = "memory_recall_events"
    __table_args__ = (
        Index("ix_memory_recall_events_run_created", "run_id", "created_at"),
        Index("ix_memory_recall_events_query_hash", "query_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    turn_id: Mapped[str | None] = mapped_column(ForeignKey("agent_turns.id"), nullable=True)
    query_hash: Mapped[str] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(40))
    shadow: Mapped[bool] = mapped_column(Boolean, default=False)
    namespace_manifest: Mapped[list] = mapped_column(JsonType, default=list)
    candidates: Mapped[list] = mapped_column(JsonType, default=list)
    selected: Mapped[list] = mapped_column(JsonType, default=list)
    excluded: Mapped[list] = mapped_column(JsonType, default=list)
    feedback: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryAuditRecord(Base):
    __tablename__ = "memory_audit_events"
    __table_args__ = (
        Index("ix_memory_audit_memory_created", "memory_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    memory_id: Mapped[str] = mapped_column(ForeignKey("memories.id"))
    event_type: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    memory: Mapped[MemoryRecord] = relationship(back_populates="audit_events")


class MemoryConsolidationJobRecord(Base):
    __tablename__ = "memory_consolidation_jobs"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_memory_consolidation_idempotency"),
        Index(
            "ix_memory_consolidation_namespace_status",
            "namespace_type",
            "namespace_id",
            "status",
        ),
        Index("ix_memory_consolidation_lease", "status", "lease_expires_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    namespace_type: Mapped[str] = mapped_column(String(40))
    namespace_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), default="queued")
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    generation: Mapped[int] = mapped_column(Integer, default=1)
    idempotency_key: Mapped[str] = mapped_column(String(160))
    input_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_manifest: Mapped[dict] = mapped_column(JsonType, default=dict)
    proposal: Mapped[dict] = mapped_column(JsonType, default=dict)
    validation: Mapped[dict] = mapped_column(JsonType, default=dict)
    profile_snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)
    model_usage: Mapped[dict] = mapped_column(JsonType, default=dict)
    publish_result: Mapped[dict] = mapped_column(JsonType, default=dict)
    error: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    rollback_of_id: Mapped[str | None] = mapped_column(
        ForeignKey("memory_consolidation_jobs.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    rollback_of: Mapped["MemoryConsolidationJobRecord | None"] = relationship(
        remote_side=[id],
        foreign_keys=[rollback_of_id],
    )


class AgentEvolutionCandidateRecord(Base):
    __tablename__ = "agent_evolution_candidates"
    __table_args__ = (
        UniqueConstraint(
            "namespace_type",
            "namespace_id",
            "candidate_key",
            "revision",
            name="uq_agent_evolution_namespace_key_revision",
        ),
        Index(
            "ix_agent_evolution_namespace_status",
            "namespace_type",
            "namespace_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    candidate_key: Mapped[str] = mapped_column(String(240))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_evolution_candidates.id"), nullable=True
    )
    candidate_type: Mapped[str] = mapped_column(String(40))
    target_component: Mapped[str] = mapped_column(String(80))
    namespace_type: Mapped[str] = mapped_column(String(40))
    namespace_id: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(40), default="draft")
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict] = mapped_column(JsonType, default=dict)
    content_digest: Mapped[str] = mapped_column(String(64))
    source_manifest: Mapped[dict] = mapped_column(JsonType, default=dict)
    source_manifest_digest: Mapped[str] = mapped_column(String(64))
    environment_constraints: Mapped[dict] = mapped_column(JsonType, default=dict)
    current_evaluation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    sources: Mapped[list["AgentEvolutionSourceRecord"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )
    evaluations: Mapped[list["AgentEvolutionEvaluationRecord"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list["AgentEvolutionAuditRecord"]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )


class AgentEvolutionSourceRecord(Base):
    __tablename__ = "agent_evolution_sources"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "source_kind",
            "source_ref",
            name="uq_agent_evolution_sources_candidate_kind_ref",
        ),
        Index("ix_agent_evolution_sources_run", "run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("agent_evolution_candidates.id"))
    source_kind: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str] = mapped_column(String(320))
    source_hash: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    memory_id: Mapped[str | None] = mapped_column(ForeignKey("memories.id"), nullable=True)
    source_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    accessible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    candidate: Mapped[AgentEvolutionCandidateRecord] = relationship(
        back_populates="sources"
    )


class AgentEvolutionEvaluationRecord(Base):
    __tablename__ = "agent_evolution_evaluations"
    __table_args__ = (
        UniqueConstraint(
            "candidate_id",
            "version",
            name="uq_agent_evolution_evaluation_version",
        ),
        Index("ix_agent_evolution_evaluation_digest", "manifest_digest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("agent_evolution_candidates.id"))
    version: Mapped[int] = mapped_column(Integer, default=1)
    manifest: Mapped[dict] = mapped_column(JsonType, default=dict)
    manifest_digest: Mapped[str] = mapped_column(String(64))
    evaluator: Mapped[str] = mapped_column(String(160))
    issuer: Mapped[str] = mapped_column(String(160))
    verdict: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    candidate: Mapped[AgentEvolutionCandidateRecord] = relationship(
        back_populates="evaluations"
    )


class AgentEvolutionAuditRecord(Base):
    __tablename__ = "agent_evolution_audit_events"
    __table_args__ = (
        Index("ix_agent_evolution_audit_candidate_created", "candidate_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("agent_evolution_candidates.id"))
    event_type: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    candidate: Mapped[AgentEvolutionCandidateRecord] = relationship(
        back_populates="audit_events"
    )


class ScheduledJobRecord(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (
        Index(
            "ix_scheduled_jobs_due",
            "enabled",
            "deleted_at",
            "next_fire_at",
            "lease_expires_at",
        ),
        Index("ix_scheduled_jobs_target", "target_task_id", "kind"),
        UniqueConstraint("system_key", name="uq_scheduled_jobs_system_key"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(240))
    kind: Mapped[str] = mapped_column(String(40), default="agent")
    system_key: Mapped[str | None] = mapped_column(String(320), nullable=True)
    system_managed: Mapped[bool] = mapped_column(Boolean, default=False)
    owner_principal: Mapped[str | None] = mapped_column(String(240), nullable=True)
    target_task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id"), nullable=True
    )
    prompt: Mapped[str] = mapped_column(Text)
    schedule_type: Mapped[str] = mapped_column(String(40))
    schedule: Mapped[dict] = mapped_column(JsonType, default=dict)
    timezone: Mapped[str] = mapped_column(String(120), default="UTC")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    misfire_policy: Mapped[str] = mapped_column(String(40), default="skip")
    misfire_grace_seconds: Mapped[int] = mapped_column(Integer, default=300)
    overlap_policy: Mapped[str] = mapped_column(String(40), default="skip")
    execution: Mapped[dict] = mapped_column(JsonType, default=dict)
    heartbeat: Mapped[dict] = mapped_column(JsonType, default=dict)
    next_fire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_fire_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(240), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ScheduledJobRunRecord(Base):
    __tablename__ = "scheduled_job_runs"
    __table_args__ = (
        UniqueConstraint(
            "job_id",
            "scheduled_for",
            name="uq_scheduled_job_runs_job_scheduled_for",
        ),
        UniqueConstraint(
            "idempotency_key",
            name="uq_scheduled_job_runs_idempotency_key",
        ),
        Index("ix_scheduled_job_runs_job_created", "job_id", "created_at"),
        Index("ix_scheduled_job_runs_status_claimed", "status", "claimed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    job_id: Mapped[str] = mapped_column(ForeignKey("scheduled_jobs.id"))
    scheduled_for: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(320))
    trigger_type: Mapped[str] = mapped_column(String(40), default="scheduled")
    status: Mapped[str] = mapped_column(String(40), default="claimed")
    claimed_by: Mapped[str | None] = mapped_column(String(240), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    outcome: Mapped[dict] = mapped_column(JsonType, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
