from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.db.models.executions import NodeExecutionRecord
    from app.infrastructure.db.models.permissions import ToolCallRecord
    from app.infrastructure.db.models.runs import RunRecord

from datetime import datetime

from sqlalchemy import (
    Boolean,
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


class PlanRecord(AstraOrmRecordBase):
    __tablename__ = "plans"
    __table_args__ = (
        UniqueConstraint("run_id", "version", name="uq_plans_run_version"),
        Index("ix_plans_run_status", "run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_execution_id: Mapped[str | None] = mapped_column(ForeignKey("agent_executions.id"), nullable=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(40), default="planned")
    supersedes_plan_id: Mapped[str | None] = mapped_column(ForeignKey("plans.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="plans", foreign_keys=[run_id])
    nodes: Mapped[list[PlanNodeRecord]] = relationship(
        back_populates="plan", order_by="PlanNodeRecord.index", cascade="all, delete-orphan"
    )
    edges: Mapped[list[PlanEdgeRecord]] = relationship(back_populates="plan", cascade="all, delete-orphan")
    executions: Mapped[list[NodeExecutionRecord]] = relationship(back_populates="plan")


class PlanNodeRecord(AstraOrmRecordBase):
    __tablename__ = "plan_nodes"
    __table_args__ = (
        UniqueConstraint("plan_id", "node_key", name="uq_plan_nodes_plan_key"),
        UniqueConstraint("plan_id", "index", name="uq_plan_nodes_plan_index"),
        Index("ix_plan_nodes_plan_status", "plan_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    plan_id: Mapped[str] = mapped_column(ForeignKey("plans.id"))
    agent_execution_id: Mapped[str | None] = mapped_column(ForeignKey("agent_executions.id"), nullable=True)
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
    outgoing_edges: Mapped[list[PlanEdgeRecord]] = relationship(
        back_populates="predecessor", foreign_keys="PlanEdgeRecord.predecessor_id"
    )
    incoming_edges: Mapped[list[PlanEdgeRecord]] = relationship(
        back_populates="successor", foreign_keys="PlanEdgeRecord.successor_id"
    )
    tool_calls: Mapped[list[ToolCallRecord]] = relationship(back_populates="plan_node")
    executions: Mapped[list[NodeExecutionRecord]] = relationship(back_populates="plan_node")


class PlanEdgeRecord(AstraOrmRecordBase):
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
    predecessor: Mapped[PlanNodeRecord] = relationship(back_populates="outgoing_edges", foreign_keys=[predecessor_id])
    successor: Mapped[PlanNodeRecord] = relationship(back_populates="incoming_edges", foreign_keys=[successor_id])
