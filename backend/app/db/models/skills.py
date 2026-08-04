from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.db.models.runs import RunRecord

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.model_base import Base, JsonType, utc_now, uuid_str


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
    skill_id: Mapped[str | None] = mapped_column(ForeignKey("skills.id"), nullable=True)
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

    draft: Mapped[SkillDraftRecord | None] = relationship(
        back_populates="skill", uselist=False, cascade="all, delete-orphan"
    )
    revisions: Mapped[list[SkillRevisionRecord]] = relationship(
        back_populates="skill",
        cascade="all, delete-orphan",
        order_by="SkillRevisionRecord.version",
    )


class SkillDraftRecord(Base):
    __tablename__ = "skill_drafts"

    skill_id: Mapped[str] = mapped_column(ForeignKey("skills.id"), primary_key=True)
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
