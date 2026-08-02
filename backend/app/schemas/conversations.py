from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.agent import RunView


class ConversationSummary(BaseModel):
    id: str
    title: str
    title_source: str = "auto"
    preferred_answer_mode: Literal["standard", "trusted"] = "standard"
    pinned_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    last_run_status: str | None = None
    last_message_preview: str = ""
    has_active_share: bool = False


class CommandMessageView(BaseModel):
    id: str
    command: str
    content: str
    arguments: str = ""
    after_run_count: int = Field(default=0, ge=0)
    created_at: datetime


class ConversationView(ConversationSummary):
    runs: list[RunView] = Field(default_factory=list)
    command_messages: list[CommandMessageView] = Field(default_factory=list)


class ConversationCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    preferred_answer_mode: Literal["standard", "trusted"] = "standard"


class ConversationUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=240)
    pinned: bool | None = None
    preferred_answer_mode: Literal["standard", "trusted"] | None = None


class PublicProcessItem(BaseModel):
    kind: Literal["reasoning", "tool", "reflection", "verification"]
    title: str
    detail: str = ""
    status: Literal["completed", "failed", "cancelled"] = "completed"


class PublicMessage(BaseModel):
    role: Literal["user", "assistant", "process"]
    content: str = ""
    items: list[PublicProcessItem] = Field(default_factory=list)


class SharedConversation(BaseModel):
    title: str
    messages: list[PublicMessage]
    shared_at: datetime
    updated_at: datetime


class ConversationShareView(BaseModel):
    url: str
    created_at: datetime
    updated_at: datetime


class ConversationShareSummary(ConversationShareView):
    conversation_id: str
    title: str
    message_count: int = 0


class ContextUsageItem(BaseModel):
    kind: Literal["system", "summary", "conversation", "draft", "output_reserve"]
    tokens: int
    item_count: int = 0


class ContextWindowStatus(BaseModel):
    provider: str
    model: str
    window_tokens: int
    max_output_tokens: int | None = None
    context_source: Literal["catalog", "fallback"]
    context_verified: bool
    context_documentation_url: str | None = None
    available_input_tokens: int
    used_tokens: int
    remaining_tokens: int
    usage_ratio: float
    auto_compact_ratio: float
    status: Literal["normal", "warning", "compact_required", "overflow"]
    estimated: bool = True
    summary_active: bool = False
    visible_run_count: int = 0
    folded_run_count: int = 0
    breakdown: list[ContextUsageItem] = Field(default_factory=list)
    last_action: Literal["compact", "clear", "auto_compact"] | None = None
    last_action_at: datetime | None = None


class SlashSystemCommand(BaseModel):
    name: Literal["compact", "clear", "schedule", "heartbeat", "subagent"]
    command: str
    description: str
    effect: Literal[
        "compact_context",
        "clear_context",
        "manage_schedules",
        "manage_heartbeat",
        "start_subagent_run",
    ]
    argument_mode: Literal["none", "optional", "required"]
    default_arguments: str = ""
    usage: str
    side_effect: Literal["read", "write", "mixed"]
    available: bool = True
    execution_mode: Literal["host", "run"] = "host"
    unavailable_reason: str | None = None


class SlashCommandRequest(BaseModel):
    arguments: str = Field(default="", max_length=40_000)


class SlashCommandResult(BaseModel):
    command: str
    message: str
    context: ContextWindowStatus
    details: dict[str, Any] = Field(default_factory=dict)
    user_message: CommandMessageView
