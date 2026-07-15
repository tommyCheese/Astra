import uuid
from datetime import datetime, timezone

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


class ToolSettingRecord(Base):
    __tablename__ = "tool_settings"

    tool_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class ConversationStrategyPreferenceRecord(Base):
    __tablename__ = "conversation_strategy_preferences"

    id: Mapped[str] = mapped_column(String(40), primary_key=True, default="default")
    reasoning_effort: Mapped[str] = mapped_column(String(40), nullable=False)
    max_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    planning_strategy: Mapped[str] = mapped_column(String(40), nullable=False)
    reflection_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reflection_trigger: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    title: Mapped[str] = mapped_column(String(240))
    description: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="created")
    priority: Mapped[str | None] = mapped_column(String(40), nullable=True)
    risk_level: Mapped[str | None] = mapped_column(String(40), nullable=True)
    title_source: Mapped[str] = mapped_column(String(20), default="auto")
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    runs: Mapped[list["RunRecord"]] = relationship(back_populates="task")
    share: Mapped["ConversationShareRecord | None"] = relationship(back_populates="conversation", uselist=False)


class ConversationShareRecord(Base):
    __tablename__ = "conversation_shares"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", name="uq_conversation_shares_conversation_id"
        ),
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


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"))
    status: Mapped[str] = mapped_column(String(40), default="created")
    mode: Mapped[str] = mapped_column(String(80), default="web_data_query")
    model_policy: Mapped[dict] = mapped_column(JsonType, default=dict)
    agent_profile_snapshot: Mapped[dict] = mapped_column(JsonType, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    current_step_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    reasoning_policy: Mapped[dict] = mapped_column(JsonType, default=dict)
    task_contract: Mapped[dict] = mapped_column(JsonType, default=dict)
    plan_graph: Mapped[dict] = mapped_column(JsonType, default=dict)
    agent_state: Mapped[dict] = mapped_column(JsonType, default=dict)
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    terminal_reason: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    waiting_state: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    task_adapter: Mapped[str] = mapped_column(String(80), default="web")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    task: Mapped[TaskRecord] = relationship(back_populates="runs")
    steps: Mapped[list["StepRecord"]] = relationship(
        back_populates="run", order_by="StepRecord.index"
    )
    tool_calls: Mapped[list["ToolCallRecord"]] = relationship(back_populates="run")
    artifacts: Mapped[list["ArtifactRecord"]] = relationship(back_populates="run")
    events: Mapped[list["RunEventRecord"]] = relationship(
        back_populates="run", order_by="RunEventRecord.id"
    )
    turns: Mapped[list["AgentTurnRecord"]] = relationship(
        back_populates="run", order_by="AgentTurnRecord.turn_index"
    )
    memories: Mapped[list["MemoryRecord"]] = relationship(back_populates="run")
    sandbox_jobs: Mapped[list["SandboxJobRecord"]] = relationship(back_populates="run")
    model_invocations: Mapped[list["ModelInvocationRecord"]] = relationship(back_populates="run")


class ModelInvocationRecord(Base):
    __tablename__ = "model_invocations"
    __table_args__ = (
        Index("ix_model_invocations_run_created", "run_id", "created_at"),
        Index("ix_model_invocations_provider_model", "provider", "model"),
        Index("ix_model_invocations_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    turn_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(80))
    model: Mapped[str] = mapped_column(String(160))
    operation: Mapped[str] = mapped_column(String(80))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(40), default="running")
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reasoning_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(240), nullable=True)
    raw_usage: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(160), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="model_invocations")


class StepRecord(Base):
    __tablename__ = "steps"
    __table_args__ = (Index("ix_steps_run_id_index", "run_id", "index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(240))
    intent: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(40), default="pending")
    depends_on: Mapped[list] = mapped_column(JsonType, default=list)
    evidence: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord] = relationship(back_populates="steps")
    tool_calls: Mapped[list["ToolCallRecord"]] = relationship(back_populates="step")


class ToolCallRecord(Base):
    __tablename__ = "tool_calls"
    __table_args__ = (Index("ix_tool_calls_run_id", "run_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    step_id: Mapped[str | None] = mapped_column(ForeignKey("steps.id"), nullable=True)
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


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    type: Mapped[str] = mapped_column(String(80))
    path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    content_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(ForeignKey("tool_calls.id"), nullable=True)
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


class SandboxJobRecord(Base):
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


class RunEventRecord(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_run_events_run_id_id", "run_id", "id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    type: Mapped[str] = mapped_column(String(80))
    payload: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="events")


class AgentTurnRecord(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (Index("ix_agent_turns_run_id_index", "run_id", "turn_index"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"))
    turn_index: Mapped[int] = mapped_column(Integer)
    decision_type: Mapped[str] = mapped_column(String(40))
    reasoning_summary: Mapped[str] = mapped_column(Text)
    selected_tool: Mapped[str | None] = mapped_column(String(120), nullable=True)
    decision: Mapped[dict] = mapped_column(JsonType, default=dict)
    observation: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    reflection: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    memory_reads: Mapped[list] = mapped_column(JsonType, default=list)
    memory_writes: Mapped[list] = mapped_column(JsonType, default=list)
    status: Mapped[str] = mapped_column(String(40), default="created")
    evaluation: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    reflection_patch: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    state_version_before: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state_version_after: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_version: Mapped[int] = mapped_column(Integer, default=1)
    phase: Mapped[str] = mapped_column(String(40), default="created")
    idempotency_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    paused_node: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    run: Mapped[RunRecord] = relationship(back_populates="turns")


class MemoryRecord(Base):
    __tablename__ = "memories"
    __table_args__ = (Index("ix_memories_scope_kind", "scope", "kind"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.id"), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    scope: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(80))
    content: Mapped[str] = mapped_column(Text)
    structured_data: Mapped[dict] = mapped_column(JsonType, default=dict)
    provenance: Mapped[dict] = mapped_column(JsonType, default=dict)
    confidence: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    run: Mapped[RunRecord | None] = relationship(back_populates="memories")
