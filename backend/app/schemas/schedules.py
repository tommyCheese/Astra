from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScheduleType(str, Enum):
    once = "once"
    interval = "interval"
    cron = "cron"


class ScheduledJobKind(str, Enum):
    agent = "agent"
    heartbeat = "heartbeat"


class MisfirePolicy(str, Enum):
    skip = "skip"
    fire_once = "fire_once"


class OverlapPolicy(str, Enum):
    skip = "skip"


class ScheduleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: ScheduleType
    at: datetime | None = None
    interval_seconds: int | None = Field(default=None, ge=60, le=31_536_000)
    anchor_at: datetime | None = None
    expression: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def validate_shape(self) -> ScheduleSpec:
        if self.type == ScheduleType.once:
            if self.at is None or self.at.tzinfo is None:
                raise ValueError("once 计划必须提供带时区的 at")
            if any(
                value is not None
                for value in (self.interval_seconds, self.anchor_at, self.expression)
            ):
                raise ValueError("once 计划只能提供 at")
        elif self.type == ScheduleType.interval:
            if self.interval_seconds is None:
                raise ValueError("interval 计划必须提供 interval_seconds")
            if self.anchor_at is not None and self.anchor_at.tzinfo is None:
                raise ValueError("anchor_at 必须包含时区")
            if self.at is not None or self.expression is not None:
                raise ValueError("interval 计划不接受 at 或 expression")
        else:
            expression = (self.expression or "").strip()
            if len(expression.split()) != 5 or not croniter.is_valid(expression):
                raise ValueError("cron expression 必须是有效的标准五字段表达式")
            if any(value is not None for value in (self.at, self.interval_seconds, self.anchor_at)):
                raise ValueError("cron 计划只能提供 expression")
            self.expression = expression
        return self


class ActiveHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def validate_clock_time(cls, value: str) -> str:
        parts = value.split(":")
        if (
            len(parts) != 2
            or not all(part.isdigit() for part in parts)
            or not 0 <= int(parts[0]) <= 23
            or not 0 <= int(parts[1]) <= 59
        ):
            raise ValueError("时间必须使用 HH:MM 格式")
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"


class ScheduledExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer_mode: str = "standard"
    model: dict | None = None
    skill_ids: list[str] = Field(default_factory=list, max_length=8)
    permission_bundle: dict


class ScheduledJobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=240)
    target_task_id: str = Field(min_length=1, max_length=36)
    prompt: str = Field(min_length=1, max_length=40_000)
    schedule: ScheduleSpec
    timezone: str = Field(default="UTC", max_length=120)
    enabled: bool = True
    misfire_policy: MisfirePolicy = MisfirePolicy.skip
    misfire_grace_seconds: int = Field(default=300, ge=0, le=604_800)
    overlap_policy: OverlapPolicy = OverlapPolicy.skip
    execution: ScheduledExecutionConfig

    @field_validator("name", "prompt")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("字段不能为空")
        return stripped

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("timezone 必须是有效的 IANA 时区") from exc
        return value


class ScheduledJobCreateRequest(ScheduledJobCreate):
    execution: ScheduledExecutionConfig | None = None


class ScheduledJobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    target_task_id: str | None = Field(default=None, min_length=1, max_length=36)
    name: str | None = Field(default=None, min_length=1, max_length=240)
    prompt: str | None = Field(default=None, min_length=1, max_length=40_000)
    schedule: ScheduleSpec | None = None
    timezone: str | None = Field(default=None, max_length=120)
    misfire_policy: MisfirePolicy | None = None
    misfire_grace_seconds: int | None = Field(default=None, ge=0, le=604_800)
    overlap_policy: OverlapPolicy | None = None
    execution: ScheduledExecutionConfig | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return ScheduledJobCreate.validate_timezone(value)


class ScheduledJobVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class ScheduledJobManualRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str | None = Field(default=None, min_length=1, max_length=240)


class HeartbeatConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_task_id: str = Field(max_length=36)
    enabled: bool = False
    interval_seconds: int = Field(default=1_800, ge=60, le=86_400)
    timezone: str = Field(default="UTC", max_length=120)
    active_hours: ActiveHours | None = None
    prompt: str = Field(
        default=(
            "检查明确记录的未完成事项与后台结果。不要从旧对话推断重复任务。"
            "如果没有需要用户关注的内容，只回复 HEARTBEAT_OK。"
        ),
        min_length=1,
        max_length=40_000,
    )
    execution: ScheduledExecutionConfig

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        return ScheduledJobCreate.validate_timezone(value)


class HeartbeatConfigRequest(HeartbeatConfig):
    execution: ScheduledExecutionConfig | None = None


class ScheduledJobView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: ScheduledJobKind
    system_managed: bool
    owner_principal: str | None
    target_task_id: str | None
    prompt: str
    schedule_type: ScheduleType
    schedule: dict
    timezone: str
    enabled: bool
    misfire_policy: MisfirePolicy
    misfire_grace_seconds: int
    overlap_policy: OverlapPolicy
    execution: dict
    heartbeat: dict
    next_fire_at: datetime | None
    last_fire_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class ScheduledJobRunView(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    scheduled_for: datetime
    trigger_type: str
    status: str
    task_id: str | None
    run_id: str | None
    outcome: dict
    claimed_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class ScheduledDeliverableView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    job_id: str
    schedule_run_id: str
    run_id: str
    task_id: str
    kind: Literal["result", "file"]
    title: str
    summary: str | None = None
    mime_type: str | None = None
    size_bytes: int | None = None
    content_url: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
