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
from sqlalchemy.orm import Mapped, mapped_column

from app.db.model_base import Base, JsonType, utc_now, uuid_str


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
    target_task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
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
    next_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_fire_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(240), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"), nullable=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    outcome: Mapped[dict] = mapped_column(JsonType, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
