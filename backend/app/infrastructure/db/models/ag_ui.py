from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.model_base import AstraOrmRecordBase, JsonType, utc_now, uuid_str


class AgUiRunBindingRecord(AstraOrmRecordBase):
    __tablename__ = "ag_ui_run_bindings"
    __table_args__ = (
        UniqueConstraint(
            "principal_id",
            "thread_id",
            "protocol_run_id",
            name="uq_ag_ui_run_bindings_principal_thread_run",
        ),
        Index("ix_ag_ui_run_bindings_internal_run", "internal_run_id"),
        Index("ix_ag_ui_run_bindings_thread_status", "principal_id", "thread_id", "lifecycle_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    principal_id: Mapped[str] = mapped_column(String(240))
    thread_id: Mapped[str] = mapped_column(String(200))
    protocol_run_id: Mapped[str] = mapped_column(String(200))
    parent_protocol_run_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    internal_task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    internal_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    lifecycle_status: Mapped[str] = mapped_column(String(40), default="created")
    profile_version: Mapped[str] = mapped_column(String(80))
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class AgUiInterruptBindingRecord(AstraOrmRecordBase):
    __tablename__ = "ag_ui_interrupt_bindings"
    __table_args__ = (
        UniqueConstraint("interrupt_id", name="uq_ag_ui_interrupt_bindings_interrupt_id"),
        Index("ix_ag_ui_interrupt_bindings_protocol_run", "run_binding_id", "status"),
        Index("ix_ag_ui_interrupt_bindings_internal_run", "internal_run_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    interrupt_id: Mapped[str] = mapped_column(String(200))
    run_binding_id: Mapped[str] = mapped_column(ForeignKey("ag_ui_run_bindings.id"))
    internal_run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    approval_id: Mapped[str | None] = mapped_column(ForeignKey("approval_requests.id"), nullable=True)
    waiting_kind: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    expected_state_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_schema: Mapped[dict] = mapped_column(JsonType, default=dict)
    server_binding: Mapped[dict] = mapped_column(JsonType, default=dict)
    status: Mapped[str] = mapped_column(String(40), default="open")
    version: Mapped[int] = mapped_column(Integer, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_outcome: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
