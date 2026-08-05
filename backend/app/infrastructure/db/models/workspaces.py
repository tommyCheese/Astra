from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.infrastructure.db.models.conversations import TaskRecord
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


class TaskWorkspaceRecord(AstraOrmRecordBase):
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
    files: Mapped[list[WorkspaceFileRecord]] = relationship(back_populates="workspace")
    changes: Mapped[list[WorkspaceChangeRecord]] = relationship(back_populates="workspace")
    checkpoints: Mapped[list[WorkspaceCheckpointRecord]] = relationship(back_populates="workspace")


class WorkspaceFileRecord(AstraOrmRecordBase):
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


class WorkspaceCheckpointRecord(AstraOrmRecordBase):
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


class WorkspaceChangeRecord(AstraOrmRecordBase):
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


class ArtifactRecord(AstraOrmRecordBase):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    agent_execution_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_executions.id"), nullable=True
    )
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


class SandboxJobRecord(AstraOrmRecordBase):
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
