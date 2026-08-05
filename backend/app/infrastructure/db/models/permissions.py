from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.db.models.executions import NodeExecutionRecord
    from app.infrastructure.db.models.plans import PlanNodeRecord
    from app.infrastructure.db.models.runs import (
        RunRecord,
        StepRecord,
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

from app.infrastructure.db.model_base import AstraOrmRecordBase, JsonType, utc_now, uuid_str


class ToolCallRecord(AstraOrmRecordBase):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_run_id", "run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_executions.id"), nullable=True
    )
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
    node_execution: Mapped[NodeExecutionRecord | None] = relationship(back_populates="tool_calls")
    approval_request: Mapped[ApprovalRequestRecord | None] = relationship(
        back_populates="tool_call", uselist=False
    )


class ApprovalRequestRecord(AstraOrmRecordBase):
    __tablename__ = "approval_requests"
    __table_args__ = (
        Index("ix_approval_requests_run_status", "run_id", "status"),
        UniqueConstraint("tool_call_id", name="uq_approval_requests_tool_call_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_executions.id"), nullable=True
    )
    requester_identity_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_identities.id"), nullable=True
    )
    delegation_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_delegations.id"), nullable=True
    )
    turn_id: Mapped[str] = mapped_column(ForeignKey("agent_turns.id"))
    tool_call_id: Mapped[str] = mapped_column(ForeignKey("tool_calls.id"))
    node_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("node_executions.id"), nullable=True
    )
    execution_attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expected_execution_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    tool_version: Mapped[str] = mapped_column(String(40))
    frozen_input: Mapped[dict] = mapped_column(JsonType)
    input_hash: Mapped[str] = mapped_column(String(64))
    frozen_effect_plan: Mapped[dict] = mapped_column(JsonType, default=dict)
    effect_plan_hash: Mapped[str | None] = mapped_column(String(80), nullable=True)
    analyzer_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    analyzer_digest: Mapped[str | None] = mapped_column(String(120), nullable=True)
    catalog_digest: Mapped[str | None] = mapped_column(String(120), nullable=True)
    continuation_token: Mapped[str | None] = mapped_column(String(160), nullable=True)
    grant_scope: Mapped[dict] = mapped_column(JsonType, default=dict)
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


class ApprovalGrantRecord(AstraOrmRecordBase):
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


class AgentIdentityRecord(AstraOrmRecordBase):
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


class AgentDelegationRecord(AstraOrmRecordBase):
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


class ToolCatalogSnapshotRecord(AstraOrmRecordBase):
    __tablename__ = "tool_catalog_snapshots"
    __table_args__ = (UniqueConstraint("run_id", name="uq_tool_catalog_snapshots_run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    catalog: Mapped[list] = mapped_column(JsonType, default=list)
    digest: Mapped[str] = mapped_column(String(120))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    behavioral_catalog: Mapped[list] = mapped_column(JsonType, default=list)
    behavioral_digest: Mapped[str | None] = mapped_column(String(120), nullable=True)
    display_digest: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="tool_catalog_snapshot")


class CredentialGrantRecord(AstraOrmRecordBase):
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


class DataFlowStateRecord(AstraOrmRecordBase):
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
