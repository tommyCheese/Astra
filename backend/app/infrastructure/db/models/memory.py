from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.db.models.runs import RunRecord

from datetime import datetime

from sqlalchemy import (
    Boolean,
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

from app.infrastructure.db.model_base import Base, JsonType, utc_now, uuid_str


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
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("memories.id"), nullable=True)
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
    sources: Mapped[list[MemorySourceRecord]] = relationship(
        back_populates="memory",
        cascade="all, delete-orphan",
        foreign_keys="MemorySourceRecord.memory_id",
    )
    outgoing_links: Mapped[list[MemoryLinkRecord]] = relationship(
        foreign_keys="MemoryLinkRecord.source_memory_id",
        cascade="all, delete-orphan",
    )
    incoming_links: Mapped[list[MemoryLinkRecord]] = relationship(
        foreign_keys="MemoryLinkRecord.target_memory_id",
        cascade="all, delete-orphan",
    )
    audit_events: Mapped[list[MemoryAuditRecord]] = relationship(
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
    tool_call_id: Mapped[str | None] = mapped_column(ForeignKey("tool_calls.id"), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"), nullable=True)
    source_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    accessible: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    memory: Mapped[MemoryRecord] = relationship(back_populates="sources", foreign_keys=[memory_id])


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
    namespace_manifest: Mapped[list] = mapped_column(JsonType, default=list)
    candidates: Mapped[list] = mapped_column(JsonType, default=list)
    selected: Mapped[list] = mapped_column(JsonType, default=list)
    excluded: Mapped[list] = mapped_column(JsonType, default=list)
    feedback: Mapped[dict] = mapped_column(JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class MemoryAuditRecord(Base):
    __tablename__ = "memory_audit_events"
    __table_args__ = (Index("ix_memory_audit_memory_created", "memory_id", "created_at"),)

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

    rollback_of: Mapped[MemoryConsolidationJobRecord | None] = relationship(
        remote_side=[id],
        foreign_keys=[rollback_of_id],
    )
