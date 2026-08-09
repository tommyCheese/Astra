from __future__ import annotations

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


class AgentEvolutionCandidateRecord(AstraOrmRecordBase):
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
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("agent_evolution_candidates.id"), nullable=True)
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

    sources: Mapped[list[AgentEvolutionSourceRecord]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )
    evaluations: Mapped[list[AgentEvolutionEvaluationRecord]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list[AgentEvolutionAuditRecord]] = relationship(
        back_populates="candidate",
        cascade="all, delete-orphan",
    )


class AgentEvolutionSourceRecord(AstraOrmRecordBase):
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

    candidate: Mapped[AgentEvolutionCandidateRecord] = relationship(back_populates="sources")


class AgentEvolutionEvaluationRecord(AstraOrmRecordBase):
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

    candidate: Mapped[AgentEvolutionCandidateRecord] = relationship(back_populates="evaluations")


class AgentEvolutionAuditRecord(AstraOrmRecordBase):
    __tablename__ = "agent_evolution_audit_events"
    __table_args__ = (Index("ix_agent_evolution_audit_candidate_created", "candidate_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("agent_evolution_candidates.id"))
    event_type: Mapped[str] = mapped_column(String(80))
    actor: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    candidate: Mapped[AgentEvolutionCandidateRecord] = relationship(back_populates="audit_events")
