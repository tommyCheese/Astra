from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.db.models.permissions import (
        ApprovalRequestRecord,
        ToolCallRecord,
    )
    from app.infrastructure.db.models.plans import (
        PlanNodeRecord,
        PlanRecord,
    )
    from app.infrastructure.db.models.runs import (
        AgentTurnRecord,
        RunRecord,
    )

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.infrastructure.db.model_base import AstraOrmRecordBase, JsonType, utc_now, uuid_str


class NodeExecutionRecord(AstraOrmRecordBase):
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
    agent_execution_id: Mapped[str | None] = mapped_column(ForeignKey("agent_executions.id"), nullable=True)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    plan_version: Mapped[int] = mapped_column(Integer)
    plan_node_id: Mapped[str] = mapped_column(ForeignKey("plan_nodes.id"))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    dispatch_batch_id: Mapped[str] = mapped_column(String(36))
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    phase: Mapped[str] = mapped_column(String(40), default="claimed")
    status: Mapped[str] = mapped_column(String(40), default="active")
    current_slot: Mapped[str | None] = mapped_column(String(16), nullable=True, default="current")
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
    tool_calls: Mapped[list[ToolCallRecord]] = relationship(back_populates="node_execution")
    turns: Mapped[list[AgentTurnRecord]] = relationship(back_populates="node_execution")
    approval_requests: Mapped[list[ApprovalRequestRecord]] = relationship(back_populates="node_execution")
    resource_leases: Mapped[list[ResourceLeaseRecord]] = relationship(
        back_populates="node_execution", cascade="all, delete-orphan"
    )
    budget_reservations: Mapped[list[BudgetReservationRecord]] = relationship(
        back_populates="node_execution", cascade="all, delete-orphan"
    )


class ResourceLeaseRecord(AstraOrmRecordBase):
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
    node_execution: Mapped[NodeExecutionRecord] = relationship(back_populates="resource_leases")


class BudgetReservationRecord(AstraOrmRecordBase):
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
    node_execution: Mapped[NodeExecutionRecord] = relationship(back_populates="budget_reservations")


class ModelInvocationRecord(AstraOrmRecordBase):
    __tablename__ = "model_invocations"
    __table_args__ = (
        Index("ix_model_invocations_run_created", "run_id", "created_at"),
        Index("ix_model_invocations_provider_model", "provider", "model"),
        Index("ix_model_invocations_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_execution_id: Mapped[str | None] = mapped_column(ForeignKey("agent_executions.id"), nullable=True)
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


class AgentExecutionRecord(AstraOrmRecordBase):
    __tablename__ = "agent_executions"
    __table_args__ = (
        UniqueConstraint("run_id", "root_slot", name="uq_agent_executions_run_root"),
        UniqueConstraint(
            "parent_execution_id",
            "request_id",
            name="uq_agent_executions_parent_request",
        ),
        Index("ix_agent_executions_run_status", "run_id", "status"),
        Index("ix_agent_executions_parent_status", "parent_execution_id", "status"),
        Index("ix_agent_executions_recovery", "status", "heartbeat_at"),
        Index("ix_agent_executions_identity", "identity_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    parent_execution_id: Mapped[str | None] = mapped_column(ForeignKey("agent_executions.id"), nullable=True)
    parent_node_execution_id: Mapped[str | None] = mapped_column(ForeignKey("node_executions.id"), nullable=True)
    identity_id: Mapped[str | None] = mapped_column(ForeignKey("agent_identities.id"), nullable=True)
    delegation_id: Mapped[str | None] = mapped_column(ForeignKey("agent_delegations.id"), nullable=True)
    execution_type: Mapped[str] = mapped_column(String(40), default="child")
    root_slot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    request_id: Mapped[str] = mapped_column(String(160))
    depth: Mapped[int] = mapped_column(Integer, default=0)
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    contract: Mapped[dict] = mapped_column(JsonType, default=dict)
    context_manifest: Mapped[dict] = mapped_column(JsonType, default=dict)
    catalog_snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)
    budget_envelope: Mapped[dict] = mapped_column(JsonType, default=dict)
    budget_usage: Mapped[dict] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="proposed")
    phase: Mapped[str] = mapped_column(String(40), default="proposed")
    wait_reason: Mapped[str | None] = mapped_column(String(120), nullable=True)
    checkpoint: Mapped[dict] = mapped_column(JsonType, default=dict)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    error: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    fencing_token: Mapped[int] = mapped_column(Integer, default=0)
    cancellation_epoch: Mapped[int] = mapped_column(Integer, default=0)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="agent_executions")


class ContextCompactionAttemptRecord(AstraOrmRecordBase):
    __tablename__ = "context_compaction_attempts"
    __table_args__ = (
        UniqueConstraint(
            "owner_type",
            "owner_id",
            "window_number",
            "input_digest",
            "policy_version",
            name="uq_context_compaction_idempotency",
        ),
        Index("ix_context_compaction_owner_window", "owner_type", "owner_id", "window_number"),
        Index("ix_context_compaction_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    owner_type: Mapped[str] = mapped_column(String(40))
    owner_id: Mapped[str] = mapped_column(String(160))
    window_number: Mapped[int] = mapped_column(Integer, default=0)
    input_digest: Mapped[str] = mapped_column(String(160))
    policy_version: Mapped[str] = mapped_column(String(80))
    checkpoint_schema_version: Mapped[int] = mapped_column(Integer, default=2)
    implementation: Mapped[str] = mapped_column(String(40))
    generation_provider: Mapped[str | None] = mapped_column(String(120), nullable=True)
    generation_model: Mapped[str | None] = mapped_column(String(240), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="started")
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    cancellation_epoch: Mapped[int] = mapped_column(Integer, default=0)
    source_item_ids: Mapped[list] = mapped_column(JsonType, default=list)
    retained_tail_ids: Mapped[list] = mapped_column(JsonType, default=list)
    token_before: Mapped[int] = mapped_column(Integer, default=0)
    token_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    failure_stage: Mapped[str | None] = mapped_column(String(120), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    checkpoint: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    usage: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class RuntimeProfileRecord(AstraOrmRecordBase):
    __tablename__ = "runtime_profiles"

    id: Mapped[str] = mapped_column(String(80), primary_key=True, default="default")
    dependencies: Mapped[list] = mapped_column(JsonType, default=list)
    active_image: Mapped[str] = mapped_column(String(500))
    dependency_digest: Mapped[str] = mapped_column(String(160))
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    builds: Mapped[list[RuntimeBuildRecord]] = relationship(back_populates="profile", order_by="RuntimeBuildRecord.created_at")


class RuntimeBuildRecord(AstraOrmRecordBase):
    __tablename__ = "runtime_builds"
    __table_args__ = (
        Index("ix_runtime_builds_profile_created", "profile_id", "created_at"),
        Index("ix_runtime_builds_status_updated", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    profile_id: Mapped[str] = mapped_column(ForeignKey("runtime_profiles.id"))
    dependencies: Mapped[list] = mapped_column(JsonType, default=list)
    dependency_digest: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(40), default="queued")
    phase: Mapped[str] = mapped_column(String(160), default="等待构建")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    log_summary: Mapped[str] = mapped_column(Text, default="")
    staging_image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    activated_image: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    profile: Mapped[RuntimeProfileRecord] = relationship(back_populates="builds")


class AgentBudgetReservationRecord(AstraOrmRecordBase):
    __tablename__ = "agent_budget_reservations"
    __table_args__ = (
        UniqueConstraint(
            "child_execution_id",
            name="uq_agent_budget_reservations_child",
        ),
        Index(
            "ix_agent_budget_reservations_parent_status",
            "parent_execution_id",
            "status",
        ),
        Index("ix_agent_budget_reservations_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    parent_execution_id: Mapped[str] = mapped_column(ForeignKey("agent_executions.id"))
    child_execution_id: Mapped[str] = mapped_column(ForeignKey("agent_executions.id"))
    envelope: Mapped[dict] = mapped_column(JsonType, default=dict)
    parent_reserve: Mapped[dict] = mapped_column(JsonType, default=dict)
    actual_usage: Mapped[dict] = mapped_column(JsonType, default=dict)
    returned_budget: Mapped[dict] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="reserved")
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentJoinRecord(AstraOrmRecordBase):
    __tablename__ = "agent_joins"
    __table_args__ = (
        UniqueConstraint(
            "parent_execution_id",
            "join_key",
            name="uq_agent_joins_parent_key",
        ),
        UniqueConstraint(
            "parent_execution_id",
            "group_id",
            name="uq_agent_joins_parent_group",
        ),
        Index("ix_agent_joins_run_status", "run_id", "status"),
        Index("ix_agent_joins_parent_status", "parent_execution_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    parent_execution_id: Mapped[str] = mapped_column(ForeignKey("agent_executions.id"))
    consumer_plan_node_id: Mapped[str | None] = mapped_column(ForeignKey("plan_nodes.id"), nullable=True)
    join_key: Mapped[str] = mapped_column(String(160))
    group_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    policy: Mapped[str] = mapped_column(String(40))
    child_execution_ids: Mapped[list] = mapped_column(JsonType, default=list)
    required_execution_ids: Mapped[list] = mapped_column(JsonType, default=list)
    optional_execution_ids: Mapped[list] = mapped_column(JsonType, default=list)
    status: Mapped[str] = mapped_column(String(40), default="waiting")
    result: Mapped[dict] = mapped_column(JsonType, default=dict)
    consumed_parent_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="agent_joins")
