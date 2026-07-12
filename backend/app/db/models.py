import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def uuid_str() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


JsonType = JSON().with_variant(JSONB, "postgresql")


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    created_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    workspace_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="created")
    priority: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    risk_level: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    runs: Mapped[List["RunRecord"]] = relationship(back_populates="task")


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(String(40), default="created")
    mode: Mapped[str] = mapped_column(String(80), default="web_data_query")
    model_policy: Mapped[dict] = mapped_column(JsonType, default=dict)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    current_step_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    reasoning_policy: Mapped[dict] = mapped_column(JsonType, default=dict)
    task_contract: Mapped[dict] = mapped_column(JsonType, default=dict)
    plan_graph: Mapped[dict] = mapped_column(JsonType, default=dict)
    agent_state: Mapped[dict] = mapped_column(JsonType, default=dict)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    terminal_reason: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    waiting_state: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    task_adapter: Mapped[str] = mapped_column(String(80), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    task: Mapped[TaskRecord] = relationship(back_populates="runs")
    steps: Mapped[List["StepRecord"]] = relationship(back_populates="run", order_by="StepRecord.index")
    tool_calls: Mapped[List["ToolCallRecord"]] = relationship(back_populates="run")
    artifacts: Mapped[List["ArtifactRecord"]] = relationship(back_populates="run")
    events: Mapped[List["RunEventRecord"]] = relationship(back_populates="run", order_by="RunEventRecord.id")
    turns: Mapped[List["AgentTurnRecord"]] = relationship(back_populates="run", order_by="AgentTurnRecord.turn_index")
    memories: Mapped[List["MemoryRecord"]] = relationship(back_populates="run")
    sandbox_jobs: Mapped[List["SandboxJobRecord"]] = relationship(back_populates="run")


class StepRecord(Base):
    __tablename__ = "steps"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240))
    intent: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    depends_on: Mapped[list] = mapped_column(JsonType, default=list)
    evidence: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="steps")
    tool_calls: Mapped[List["ToolCallRecord"]] = relationship(back_populates="step")


class ToolCallRecord(Base):
    __tablename__ = "tool_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    step_id: Mapped[Optional[str]] = mapped_column(ForeignKey("steps.id"), nullable=True)
    tool_name: Mapped[str] = mapped_column(String(120))
    tool_version: Mapped[str] = mapped_column(String(40))
    input: Mapped[dict] = mapped_column(JsonType)
    output: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    status: Mapped[str] = mapped_column(String(40))
    permission: Mapped[str] = mapped_column(String(80))
    side_effect_level: Mapped[str] = mapped_column(String(80))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="tool_calls")
    step: Mapped[Optional[StepRecord]] = relationship(back_populates="tool_calls")


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    type: Mapped[str] = mapped_column(String(80))
    path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    content_ref: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tool_calls.id"), nullable=True)
    sandbox_job_id: Mapped[Optional[str]] = mapped_column(ForeignKey("sandbox_jobs.id"), nullable=True)
    mime_type: Mapped[Optional[str]] = mapped_column(String(160), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    checksum: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    storage_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    preview_key: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    security_status: Mapped[str] = mapped_column(String(40), default="pending")
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    metadata_: Mapped[dict] = mapped_column("metadata", JsonType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="artifacts")


class SandboxJobRecord(Base):
    __tablename__ = "sandbox_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    tool_call_id: Mapped[Optional[str]] = mapped_column(ForeignKey("tool_calls.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="queued")
    runtime_profile: Mapped[dict] = mapped_column(JsonType, default=dict)
    resource_limits: Mapped[dict] = mapped_column(JsonType, default=dict)
    input_artifact_ids: Mapped[list] = mapped_column(JsonType, default=list)
    output_artifact_ids: Mapped[list] = mapped_column(JsonType, default=list)
    executor: Mapped[str] = mapped_column(String(80))
    runtime_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    image_digest: Mapped[Optional[str]] = mapped_column(String(240), nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    error: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    stdout_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stderr_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="sandbox_jobs")


class RunEventRecord(Base):
    __tablename__ = "run_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="events")


class AgentTurnRecord(Base):
    __tablename__ = "agent_turns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    turn_index: Mapped[int] = mapped_column(Integer)
    decision_type: Mapped[str] = mapped_column(String(40))
    reasoning_summary: Mapped[str] = mapped_column(Text)
    selected_tool: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    decision: Mapped[dict] = mapped_column(JsonType, default=dict)
    observation: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    reflection: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    tool_call_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    artifact_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    memory_reads: Mapped[list] = mapped_column(JsonType, default=list)
    memory_writes: Mapped[list] = mapped_column(JsonType, default=list)
    status: Mapped[str] = mapped_column(String(40), default="created")
    evaluation: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    reflection_patch: Mapped[Optional[dict]] = mapped_column(JsonType, nullable=True)
    state_version_before: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    state_version_after: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    phase: Mapped[str] = mapped_column(String(40), default="created")
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    paused_node: Mapped[Optional[str]] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="turns")


class MemoryRecord(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[Optional[str]] = mapped_column(ForeignKey("runs.id"), nullable=True)
    workspace_id: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    created_by: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    scope: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text)
    structured_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[Optional[RunRecord]] = relationship(back_populates="memories")
