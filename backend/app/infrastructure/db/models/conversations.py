from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.db.models.runs import RunRecord
    from app.infrastructure.db.models.workspaces import TaskWorkspaceRecord

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


class ToolSettingRecord(AstraOrmRecordBase):
    __tablename__ = "tool_settings"

    tool_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConversationStrategyPreferenceRecord(AstraOrmRecordBase):
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


class TaskRecord(AstraOrmRecordBase):
    __tablename__ = "tasks"
    __table_args__ = (Index("ix_tasks_retention_scan", "pinned_at", "updated_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    workspace_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
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

    runs: Mapped[list[RunRecord]] = relationship(back_populates="task")
    share: Mapped[ConversationShareRecord | None] = relationship(
        back_populates="conversation", uselist=False
    )
    task_workspace: Mapped[TaskWorkspaceRecord | None] = relationship(
        back_populates="task", uselist=False
    )


class ConversationShareRecord(AstraOrmRecordBase):
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
