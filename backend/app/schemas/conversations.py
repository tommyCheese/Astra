from datetime import datetime
from typing import Literal

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


class ConversationView(ConversationSummary):
    runs: list[RunView] = Field(default_factory=list)


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
